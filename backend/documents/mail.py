"""E-Mail-Ingestion via IMAP (Stufe 3, siehe KONZEPT.md).

Ein konfiguriertes IMAP-Postfach wird periodisch abgerufen; Anhänge (PDF/Bilder)
landen in derselben Verarbeitungs-Pipeline wie ein Upload (OCR → Metadaten →
Klassifizierung). Der Absender wird als Correspondent-*Vorschlag* hinterlegt.

``imaplib`` und ``email`` sind Standardbibliothek – kein Extra-Requirement.
Die Funktionen hier kapseln die gesamte E-Mail-Logik; die Celery-Wrapper stehen
in ``tasks.py``.

Robustheit (Akzeptanzkriterium): Ein Fehler bei *einer* Mail bricht den Abruf
nicht ab – jede Mail wird einzeln in try/except verarbeitet und geloggt.
Idempotenz: Bereits verarbeitete Mails werden über die Message-ID (``ProcessedMail``)
übersprungen; identische Anhänge zusätzlich über den Inhalts-Hash (SHA-256).
"""
from __future__ import annotations

import contextlib
import email as email_mod
import hashlib
import imaplib
import logging
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

# Namespace für Postgres-Advisory-Locks dieses Features ("MAIL"), damit sich
# der Abruf-Lock nicht mit Advisory-Locks anderer Stellen überschneidet.
_FETCH_LOCK_NAMESPACE = 0x4D41494C


@contextlib.contextmanager
def account_fetch_lock(account_id: int):
    """Nicht-blockierender Abruf-Lock pro Konto gegen überlappende Beat-Läufe.

    Nutzt ``pg_try_advisory_lock`` (kehrt sofort zurück): Läuft für dasselbe Konto
    bereits ein Abruf, liefert der Kontextmanager ``False`` und der Aufrufer
    überspringt den Lauf. Der Lock ist verbindungsgebunden und wird im ``finally``
    – spätestens beim Verbindungsabbruch (Worker-Crash) – wieder freigegeben.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", [_FETCH_LOCK_NAMESPACE, account_id]
        )
        acquired = bool(cur.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [_FETCH_LOCK_NAMESPACE, account_id],
                )

# Anhänge, die in die Pipeline gegeben werden (Rechnungen, Scans …).
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp"}
ALLOWED_MIME_PREFIXES = ("application/pdf", "image/")

# Socket-Timeout (Sekunden) für die IMAP-Verbindung – verhindert, dass ein
# nicht antwortender Server den Abruf-Worker unbegrenzt blockiert.
IMAP_TIMEOUT = 30


def _decode(value: str | None) -> str:
    """MIME-kodierte Header (=?utf-8?...?=) in lesbaren Text wandeln."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # pragma: no cover - defensiv gegen kaputte Header
        return value


def sender_of(msg: Message) -> tuple[str, str]:
    """(Anzeigename, E-Mail-Adresse) des Absenders."""
    name, addr = parseaddr(_decode(msg.get("From", "")))
    return _decode(name) or addr, addr


def received_at_of(msg: Message):
    """Date-Header als aware datetime, falls die Mail einen verwertbaren Wert trägt."""
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_default_timezone())
    return value


def _ext_of(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


def iter_attachments(msg: Message):
    """Liefert (Dateiname, Bytes, MIME) je relevantem Anhang (PDF/Bild)."""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = _decode(part.get_filename())
        if "attachment" not in disposition and not filename:
            continue  # Inline-Textkörper etc. überspringen
        ctype = (part.get_content_type() or "").lower()
        ext = _ext_of(filename)
        if not (ctype.startswith(ALLOWED_MIME_PREFIXES) or ext in ALLOWED_EXTENSIONS):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if not filename:
            filename = f"anhang{ext or '.bin'}"
        yield filename, payload, ctype


def connect(account) -> imaplib.IMAP4:
    """Baut die IMAP-Verbindung auf und meldet sich an (Passwort aus Secret/DB)."""
    password = account.resolve_password()
    # Expliziter Timeout: ohne ihn blockiert ein toter IMAP-Server den
    # Celery-Worker unbegrenzt (der Default ist der globale Socket-Timeout, i.
    # d. R. None). 30 s reichen für Login + Fetch und geben eine Mail beim
    # nächsten Lauf wieder frei.
    if account.use_ssl:
        conn = imaplib.IMAP4_SSL(account.host, account.port, timeout=IMAP_TIMEOUT)
    else:
        conn = imaplib.IMAP4(account.host, account.port, timeout=IMAP_TIMEOUT)
    conn.login(account.username, password)
    return conn


def _apply_sender_hint(document, name: str, addr: str) -> None:
    """Absender → Correspondent-Vorschlag.

    Ist ein Korrespondent mit passendem Namen bekannt, wird er direkt zugeordnet;
    sonst wird der Absender als unverbindlicher Vorschlag in ``ai_suggestions``
    hinterlegt (überlebt dank Merge in ``ai.tasks.suggest_document_metadata``).
    """
    from .models import Correspondent

    match = Correspondent.objects.filter(name__iexact=name).first() if name else None
    if match:
        document.correspondent = match
        document.save(update_fields=["correspondent"])
        return
    hint = name or addr
    if hint:
        document.ai_suggestions = {**(document.ai_suggestions or {}), "correspondent": hint}
        document.save(update_fields=["ai_suggestions"])


def ingest_message(account, raw_bytes: bytes) -> int | None:
    """Verarbeitet eine einzelne Roh-Mail.

    Rückgabe: Anzahl importierter Anhänge, oder ``None`` wenn die Mail (per
    Message-ID) bereits verarbeitet wurde.
    """
    from . import pipeline, storage
    from .models import ProcessedMail
    from .owner import log_ingest_owner_audit, resolve_default_owner
    from .tasks import process_document_version

    msg = email_mod.message_from_bytes(raw_bytes)
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        # Fehlt die Message-ID, aus dem Inhalt eine stabile Ersatz-ID ableiten,
        # damit dieselbe Mail dennoch idempotent bleibt.
        message_id = "<no-id:%s>" % hashlib.sha256(raw_bytes).hexdigest()[:32]
    message_id = message_id[:998]  # konsistent zum DB-Feld (Query == Insert)

    if ProcessedMail.objects.filter(account=account, message_id=message_id).exists():
        return None

    subject = _decode(msg.get("Subject"))
    sender_name, sender_addr = sender_of(msg)
    received_at = received_at_of(msg)
    display_sender = (
        (sender_name and sender_addr and f"{sender_name} <{sender_addr}>")
        or sender_addr
        or sender_name
        or ""
    )

    attachment_count = 0
    imported = 0
    attachment_names: list[str] = []
    imported_documents = []
    for filename, payload, ctype in iter_attachments(msg):
        attachment_count += 1
        attachment_names.append(filename)
        sha = hashlib.sha256(payload).hexdigest()
        if pipeline.find_duplicate_version(sha):
            logger.info("Anhang %s bereits vorhanden (Hash-Dedup) – übersprungen", filename)
            continue
        path = storage.save_bytes(payload, _ext_of(filename))
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        # Owner-Auflösung (STOAA-295): Konto-Owner hat Vorrang. Ist er leer,
        # greift der konfigurierte ``MAIL_DEFAULT_OWNER``, sonst wären die
        # Dokumente für Nicht-Admins durch die Owner-Isolation (STOAA-7)
        # unsichtbar. Bleibt owner=None, ist das bewusstes Admin-Triage (siehe
        # MailAccount-Docstring) – ``log_ingest_owner_audit`` macht das explizit.
        owner = account.owner
        fallback_used = False
        if owner is None:
            owner = resolve_default_owner(getattr(settings, "MAIL_DEFAULT_OWNER", ""))
            fallback_used = owner is not None
        document, version = pipeline.create_document_from_file(
            str(path),
            title=title or subject or "E-Mail-Anhang",
            # ``owner`` setzt auch DocumentVersion.created_by + AuditLogEntry.actor.
            owner=owner,
            mime=ctype,
            size=len(payload),
            ingest_source="mail",
        )
        log_ingest_owner_audit(
            document,
            owner=owner,
            fallback_used=fallback_used,
            source="mail",
            reason="account_owner_leer",
        )
        # Hash sofort setzen, damit weitere identische Anhänge im selben Lauf
        # zuverlässig dedupliziert werden (die OCR-Pipeline berechnet ihn später
        # aus der Datei erneut – identischer Wert).
        version.sha256 = sha
        version.save(update_fields=["sha256"])
        # Betreff + Absender der Quell-Mail am Dokument hinterlegen, damit die
        # (asynchrone) Rule-Engine per subject_contains/from_contains darauf
        # matchen kann. Vor dem Enqueue setzen, sonst läuft die Klassifizierung
        # ggf. bevor die Felder persistiert sind.
        document.mail_subject = (subject or "")[:512]
        document.mail_sender = display_sender[:512]
        document.save(update_fields=["mail_subject", "mail_sender"])
        _apply_sender_hint(document, sender_name, sender_addr)
        process_document_version.delay(version.id)
        imported_documents.append(document)
        imported += 1

    if imported == 0:
        mail_status = ProcessedMail.Status.IGNORED
    elif imported < attachment_count:
        mail_status = ProcessedMail.Status.PARTIAL
    else:
        mail_status = ProcessedMail.Status.IMPORTED

    processed_mail = ProcessedMail.objects.create(
        account=account,
        message_id=message_id,
        subject=subject[:512],
        sender=display_sender[:512],
        received_at=received_at,
        status=mail_status,
        attachment_count=attachment_count,
        imported_count=imported,
        attachment_names=attachment_names,
    )
    if imported_documents:
        processed_mail.documents.add(*imported_documents)
    return imported


def fetch_account(account) -> dict:
    """Ruft ein Konto ab: ungelesene Mails verarbeiten, als gelesen markieren.

    Setzt ``\\Seen`` erst nach erfolgreicher Verarbeitung; scheitert eine Mail,
    bleibt sie ungelesen und wird beim nächsten Lauf erneut versucht (der
    Hash-/Message-ID-Dedup verhindert Doppel-Import).
    """
    stats = {"account_id": account.id, "mails": 0, "imported": 0, "skipped": 0, "errors": 0}

    try:
        conn = connect(account)
    except Exception as exc:
        logger.exception("IMAP-Verbindung fehlgeschlagen für %s", account)
        account.last_error = f"{type(exc).__name__}: {exc}"
        account.last_checked_at = timezone.now()
        account.save(update_fields=["last_error", "last_checked_at"])
        stats["status"] = "connect_error"
        stats["error"] = str(exc)
        return stats

    try:
        conn.select(account.folder)
        typ, data = conn.search(None, "UNSEEN")
        uids = data[0].split() if data and data[0] else []
        for uid in uids:
            try:
                typ, msg_data = conn.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                result = ingest_message(account, raw)
                if result is None:
                    stats["skipped"] += 1
                else:
                    stats["mails"] += 1
                    stats["imported"] += result
                conn.store(uid, "+FLAGS", "\\Seen")
            except Exception:
                stats["errors"] += 1
                logger.exception("Fehler beim Verarbeiten einer Mail (uid=%s, %s)", uid, account)
                continue
    finally:
        try:
            conn.logout()
        except Exception:  # pragma: no cover
            pass

    account.last_checked_at = timezone.now()
    account.last_error = ""
    account.save(update_fields=["last_checked_at", "last_error"])
    stats["status"] = "ok"
    return stats
