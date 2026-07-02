"""Nutzer und Rollen.

Bewusst einfach gehalten (Familie, 2–3 Nutzer): drei Rollen statt eines
feingranularen Enterprise-Rechtemodells. Feinere Rechte pro Tag/Ablagepfad
lassen sich später als eigene ACL-Ebene ergänzen (siehe KONZEPT.md §5).
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    USER = "user", "Nutzer"
    GUEST = "guest", "Gast (nur Lesen)"


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
