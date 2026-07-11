from django.db import migrations

from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    """Aktiviert die pgvector-Extension (Fundament der semantischen Suche).

    Legt ``CREATE EXTENSION IF NOT EXISTS vector`` an. Setzt das pgvector-Postgres-
    Image voraus (deploy/k8s/base/postgres.yaml, CI: backend/ci/run-tests.sh).
    Der DB-User ist im pgvector-Image Superuser und darf die Extension anlegen.
    """

    dependencies = [
        ("documents", "0038_asn_nullable"),
    ]

    operations = [
        VectorExtension(),
    ]
