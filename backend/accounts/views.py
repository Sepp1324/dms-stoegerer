from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import Household, User
from .serializers import HouseholdSerializer, UserChoiceSerializer, UserSerializer


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


def _member_household_or_404(user, pk):
    """Der Haushalt mit ``pk``, sofern ``user`` Mitglied ist – sonst 404."""
    household = user.households.filter(pk=pk).first()
    if household is None:
        raise NotFound("Haushalt nicht gefunden oder keine Mitgliedschaft.")
    return household


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def households(request):
    """GET: eigener Haushalt (oder ``null``). POST: Haushalt anlegen.

    Ein Nutzer ist in höchstens einem Haushalt (Familien-Invariante). Der Ersteller
    wird automatisch Mitglied.
    """
    if request.method == "GET":
        household = request.user.households.first()
        return Response(HouseholdSerializer(household).data if household else None)

    if not request.user.can_write:
        return Response(
            {"detail": "Keine Schreibberechtigung (Gast-Rolle)."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if request.user.households.exists():
        return Response(
            {"detail": "Du bist bereits Mitglied eines Haushalts."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    name = str(request.data.get("name", "")).strip()
    if not name:
        return Response(
            {"detail": "Feld 'name' ist erforderlich."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    household = Household.objects.create(name=name[:120], created_by=request.user)
    household.members.add(request.user)
    return Response(HouseholdSerializer(household).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def household_add_member(request, pk):
    """Fügt einen Nutzer (per ``username``) dem eigenen Haushalt hinzu."""
    household = _member_household_or_404(request.user, pk)
    username = str(request.data.get("username", "")).strip()
    if not username:
        return Response(
            {"detail": "Feld 'username' ist erforderlich."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    target = User.objects.filter(username__iexact=username, is_active=True).first()
    if target is None:
        return Response(
            {"detail": f"Kein aktiver Nutzer '{username}'."},
            status=status.HTTP_404_NOT_FOUND,
        )
    if target.households.filter(pk=household.pk).exists():
        return Response(HouseholdSerializer(household).data)  # bereits drin → idempotent
    if target.households.exists():
        return Response(
            {"detail": f"'{username}' ist bereits Mitglied eines anderen Haushalts."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    household.members.add(target)
    return Response(HouseholdSerializer(household).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def household_leave(request, pk):
    """Verlässt den eigenen Haushalt; ist er danach leer, wird er gelöscht."""
    household = _member_household_or_404(request.user, pk)
    household.members.remove(request.user)
    if not household.members.exists():
        household.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
