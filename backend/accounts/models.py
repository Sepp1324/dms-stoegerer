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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Haushalt"
        verbose_name_plural = "Haushalte"

    def __str__(self) -> str:
        return self.name


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
