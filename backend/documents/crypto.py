"""Symmetrische Verschlüsselung für in der DB abgelegte Geheimnisse (STOAA-212).

Wird für das ``MailAccount.password``-Feld verwendet: Das App-Passwort eines
IMAP-Postfachs soll **nicht im Klartext** in der Datenbank liegen. Der
bevorzugte Weg bleibt ``MailAccount.password_env`` (Passwort aus einem
k8s-Secret, gar nicht in der DB) – wird dennoch ein Passwort direkt hinterlegt,
verschlüsseln wir es hier at-rest mit Fernet (AES-128-CBC + HMAC).

Schlüsselableitung: Aus ``settings.SECRET_KEY`` per SHA-256 → 32 Byte →
urlsafe-base64. Damit ist kein zusätzliches Key-Management nötig; wer den
SECRET_KEY rotiert, muss hinterlegte Passwörter neu setzen (dokumentierte
Konsequenz, konsistent mit Djangos übriger SECRET_KEY-Abhängigkeit).
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> str:
    """Klartext → Fernet-Token (str). Leerstring bleibt Leerstring."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    """Fernet-Token → Klartext.

    Ist ``value`` kein gültiges Token (z. B. Alt-Datenbestand mit
    Klartext-Passwort aus der Zeit vor STOAA-212), wird es unverändert
    zurückgegeben – so bleibt die Migration verlustfrei und rückwärtskompatibel.
    """
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value


def is_encrypted(value: str) -> bool:
    """True, wenn ``value`` ein von uns erzeugtes Fernet-Token ist."""
    if not value:
        return False
    try:
        _fernet().decrypt(value.encode("ascii"))
        return True
    except (InvalidToken, ValueError):
        return False
