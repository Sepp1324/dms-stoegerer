import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Haushalts-Beitritt mit beidseitiger Zustimmung (P1).

    Additiv: neues ``owner``-FK + ``join_code`` auf Household, neues Modell
    ``HouseholdJoinRequest`` und zwei partielle Unique-Constraints. Keine
    Daten werden verändert/gelöscht (PVC-sicher).
    """

    dependencies = [
        ("accounts", "0002_household"),
    ]

    operations = [
        migrations.AddField(
            model_name="household",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owned_households",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="household",
            name="join_code",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=32
            ),
        ),
        migrations.AddConstraint(
            model_name="household",
            constraint=models.UniqueConstraint(
                condition=models.Q(("join_code__gt", "")),
                fields=("join_code",),
                name="uniq_household_join_code",
            ),
        ),
        migrations.CreateModel(
            name="HouseholdJoinRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Offen"),
                            ("approved", "Bestätigt"),
                            ("rejected", "Abgelehnt"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "household",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="join_requests",
                        to="accounts.household",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="join_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Haushalts-Beitrittsanfrage",
                "verbose_name_plural": "Haushalts-Beitrittsanfragen",
            },
        ),
        migrations.AddConstraint(
            model_name="householdjoinrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("household", "user"),
                name="uniq_pending_join_request",
            ),
        ),
    ]
