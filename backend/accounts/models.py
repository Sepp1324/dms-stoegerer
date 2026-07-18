"""Nutzer und Rollen.

Bewusst einfach gehalten (Familie, 2–3 Nutzer): drei Rollen statt eines
feingranularen Enterprise-Rechtemodells. Feinere Rechte pro Tag/Ablagepfad
lassen sich später als eigene ACL-Ebene ergänzen (siehe KONZEPT.md §5).
"""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    USER = "user", "Nutzer"
    GUEST = "guest", "Gast (nur Lesen)"


class Household(models.Model):
    """Familie/Haushalt: eine Gruppe von Nutzern, die Dokumente teilen können.

    Bewusst simpel für den Familien-Einsatz: Ein Nutzer ist in höchstens EINEM
    Haushalt (Invariante über die Beitritts-Endpunkte durchgesetzt), damit die
    Freigabe-Semantik eindeutig bleibt (ein für den Haushalt freigegebenes
    Dokument ist für genau die Mitglieder DIESES Haushalts lesbar).

    Beitritt mit beidseitiger Zustimmung (STOAA/P1): NIEMAND wird ohne eigene
    Zustimmung Mitglied. Der ``owner`` (der einzige Admin, per Default der
    Ersteller) teilt einen ``join_code`` out-of-band; ein Interessent stellt
    damit eine Beitrittsanfrage (``HouseholdJoinRequest``), die der ``owner``
    bestätigt. Erst die Bestätigung erzeugt die Mitgliedschaft – und damit die
    gegenseitige Sichtbarkeit ``shared_with_household``-freigegebener Dokumente.
    """

    name = models.CharField(max_length=120)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="households", blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Einziger Admin des Haushalts (verwaltet Code + Beitrittsanfragen). Per
    # Default der Ersteller; wechselt, wenn der Owner den Haushalt verlässt.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_households",
    )
    # Beitritts-Code: der Owner teilt ihn bewusst; nur wer ihn kennt, kann eine
    # Beitrittsanfrage stellen. ``unique`` (bei gesetztem Wert), rotier-/löschbar.
    join_code = models.CharField(
        max_length=32, blank=True, default="", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Haushalt"
        verbose_name_plural = "Haushalte"
        constraints = [
            # Codes sind eindeutig – aber nur, wenn gesetzt (leere Codes dürfen
            # sich beliebig oft „wiederholen").
            models.UniqueConstraint(
                fields=["join_code"],
                condition=models.Q(join_code__gt=""),
                name="uniq_household_join_code",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class JoinRequestStatus(models.TextChoices):
    PENDING = "pending", "Offen"
    APPROVED = "approved", "Bestätigt"
    REJECTED = "rejected", "Abgelehnt"


class HouseholdJoinRequest(models.Model):
    """Beitrittsanfrage eines Nutzers an einen Haushalt (ziel-initiiert).

    Consent-Kette (P1): Der Interessent stellt die Anfrage selbst (mit dem vom
    Owner geteilten ``join_code``) → eigene Zustimmung. Der Owner bestätigt →
    Admin-Zustimmung. Erst ``approve`` fügt das Mitglied hinzu. Ohne diesen
    Umweg konnte früher jedes Mitglied jeden Fremden ohne dessen Zutun in den
    Haushalt ziehen.
    """

    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="join_requests"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="join_requests"
    )
    status = models.CharField(
        max_length=16,
        choices=JoinRequestStatus.choices,
        default=JoinRequestStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "Haushalts-Beitrittsanfrage"
        verbose_name_plural = "Haushalts-Beitrittsanfragen"
        constraints = [
            # Höchstens EINE offene Anfrage je (Haushalt, Nutzer) – erneutes
            # Anfragen ist idempotent, entschiedene Anfragen bleiben als Historie.
            models.UniqueConstraint(
                fields=["household", "user"],
                condition=models.Q(status="pending"),
                name="uniq_pending_join_request",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.household} ({self.status})"


class User(AbstractUser):
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.USER,
        verbose_name="Rolle",
    )

    class Meta:
        verbose_name = "Nutzer"
        verbose_name_plural = "Nutzer"

    @property
    def is_dms_admin(self) -> bool:
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def can_write(self) -> bool:
        """Gäste dürfen nur lesen."""
        return self.role in (Role.ADMIN, Role.USER) or self.is_superuser
