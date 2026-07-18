from rest_framework import serializers

from .models import Household, HouseholdJoinRequest, JoinRequestStatus, User


class HouseholdSerializer(serializers.ModelSerializer):
    """Haushalt inkl. Mitgliederliste (schmale User-Repräsentation).

    Owner-only-Felder (``join_code``, ``pending_requests``) werden NUR dem Owner
    ausgeliefert – der Request-Kontext entscheidet. Ohne Kontext (z. B. interne
    Nutzung) bleiben sie leer/0.
    """

    members = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    join_code = serializers.SerializerMethodField()
    pending_requests = serializers.SerializerMethodField()

    class Meta:
        model = Household
        fields = (
            "id",
            "name",
            "members",
            "owner",
            "is_owner",
            "join_code",
            "pending_requests",
            "created_at",
        )
        read_only_fields = fields

    def _current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def _is_owner(self, obj) -> bool:
        user = self._current_user()
        return bool(user and user.is_authenticated and obj.owner_id == user.id)

    def get_members(self, obj) -> list:
        return [
            {"id": u.id, "username": u.username, "email": u.email}
            for u in obj.members.all().order_by("username")
        ]

    def get_owner(self, obj):
        if obj.owner_id is None:
            return None
        return {"id": obj.owner_id, "username": obj.owner.username}

    def get_is_owner(self, obj) -> bool:
        return self._is_owner(obj)

    def get_join_code(self, obj):
        # Nur der Owner darf den Code sehen (er allein verwaltet Beitritte).
        return obj.join_code if self._is_owner(obj) else None

    def get_pending_requests(self, obj) -> int:
        if not self._is_owner(obj):
            return 0
        return obj.join_requests.filter(status=JoinRequestStatus.PENDING).count()


class HouseholdJoinRequestSerializer(serializers.ModelSerializer):
    """Beitrittsanfrage inkl. schmaler Nutzer-Repräsentation."""

    user = serializers.SerializerMethodField()

    class Meta:
        model = HouseholdJoinRequest
        fields = ("id", "user", "status", "created_at")
        read_only_fields = fields

    def get_user(self, obj) -> dict:
        return {"id": obj.user_id, "username": obj.user.username}


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
