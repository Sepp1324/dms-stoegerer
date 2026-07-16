from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "documents"
    verbose_name = "Dokumente"

    def ready(self) -> None:
        # Signal-Handler registrieren (psychosr-Auto-Trigger u. a.)
        from . import signals  # noqa: F401
