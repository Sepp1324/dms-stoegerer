from rest_framework import serializers

from .models import Household, User


class HouseholdSerializer(serializers.ModelSerializer):
    """Haushalt inkl. Mitgliederliste (schmale User-Repräsentation)."""

    members = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = ("id", "name", "members", "created_at")
        read_only_fields = fields

    def get_members(self, obj) -> list:
        return [
            {"id": u.id, "username": u.username, "email": u.email}
            for u in obj.members.all().order_by("username")
        ]


class UserChoiceSerializer(serializers.ModelSerializer):
    """Schmale Auswahl-Repräsentation für Empfänger-Dropdowns (STOAA-221).

    Nur die zur Anzeige/Zuordnung nötigen Felder – keine Rollen- oder
    Rechteinformationen, da dies rein als Auswahlliste (z. B. Standard-Empfänger
    eines Mailkontos) dient.
    """

    class Meta:
        model = User
        fields = ("id", "username", "email")


class UserSerializer(serializers.ModelSerializer):
    is_dms_admin = serializers.BooleanField(read_only=True)
    can_write = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "is_dms_admin",
            "can_write",
        )
        read_only_fields = ("id", "role")
