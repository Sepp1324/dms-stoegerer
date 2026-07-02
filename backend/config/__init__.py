# Celery-App beim Start von Django verfügbar machen
from .celery import app as celery_app

__all__ = ("celery_app",)
