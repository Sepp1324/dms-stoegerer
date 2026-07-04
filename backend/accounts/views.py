from rest_framework.decorators import api_view, permission_classes
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import User
from .serializers import UserChoiceSerializer, UserSerializer


class IsDmsAdmin(BasePermission):
    """Nur DMS-Administratoren (``is_dms_admin``).

    Bewusst lokal in ``accounts`` gehalten (statt Import aus ``documents.views``),
    um die App entkoppelt zu lassen und Import-Zyklen zu vermeiden.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return bool(getattr(request.user, "is_dms_admin", False))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    """Gibt das Profil des angemeldeten Nutzers zurück."""
    return Response(UserSerializer(request.user).data)


class UserListView(ListAPIView):
    """Read-only Auswahlliste der DMS-Nutzer (STOAA-221).

    Liefert ``[{id, username, email}]`` für Empfänger-Dropdowns (z. B. den
    Standard-Empfänger eines Mailkontos, STOAA-215). Nur DMS-Admins, da bisher
    nur die Mailkonto-Verwaltung diese Liste nutzt; keine sensiblen Felder,
    kein Schreib-/Detailzugriff. Nur aktive Nutzer, stabil nach ``username``
    sortiert für eine vorhersehbare Dropdown-Reihenfolge.
    """

    serializer_class = UserChoiceSerializer
    permission_classes = [IsDmsAdmin]
    pagination_class = None
    queryset = User.objects.filter(is_active=True).order_by("username")
