"""Celery-App für asynchrone Aufgaben (OCR, Klassifizierung, E-Mail-Abruf)."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("dms")
# Konfiguration aus den Django-Settings (Präfix CELERY_)
app.config_from_object("django.conf:settings", namespace="CELERY")
# Tasks aller installierten Apps automatisch entdecken
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
