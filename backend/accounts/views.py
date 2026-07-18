import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import Household, HouseholdJoinRequest, JoinRequestStatus, User
from .serializers import (
    HouseholdJoinRequestSerializer,
    HouseholdSerializer,
    UserChoiceSerializer,
    UserSerializer,
)


def _household_data(household, request):
    """Serialisiert einen Haushalt inkl. Request-Kontext (owner-only-Felder)."""
    return HouseholdSerializer(household, context={"request": request}).data


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


def _owner_household_or_404(user, pk):
    """Der Haushalt mit ``pk``, sofern ``user`` dessen Owner (Admin) ist.

    Bewusst 404 (nicht 403) für Nicht-Owner, damit die Existenz fremder
    Haushalte nicht durchsickert.
    """
    household = Household.objects.filter(pk=pk, owner=user).first()
    if household is None:
        raise NotFound("Haushalt nicht gefunden oder keine Admin-Rechte.")
    return household


def _new_join_code() -> str:
    """Kurzer, URL-sicherer Beitritts-Code (per Owner geteilt)."""
    return secrets.token_urlsafe(9)[:12]


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def households(request):
    """GET: eigener Haushalt (oder ``null``). POST: Haushalt anlegen.

    Ein Nutzer ist in höchstens einem Haushalt (Familien-Invariante). Der Ersteller
    wird automatisch Mitglied UND Owner (einziger Admin).
    """
    if request.method == "GET":
        household = request.user.households.first()
        return Response(_household_data(household, request) if household else None)

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
    household = Household.objects.create(
        name=name[:120], created_by=request.user, owner=request.user
    )
    household.members.add(request.user)
    return Response(
        _household_data(household, request), status=status.HTTP_201_CREATED
    )


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def household_join_code(request, pk):
    """Owner erzeugt/rotiert (POST) oder löscht (DELETE) den Beitritts-Code."""
    household = _owner_household_or_404(request.user, pk)
    if request.method == "DELETE":
        household.join_code = ""
        household.save(update_fields=["join_code"])
        return Response(_household_data(household, request))

    # Kollisionen sind bei 12 zufälligen url-safe-Zeichen extrem unwahrscheinlich,
    # werden aber sicherheitshalber neu gewürfelt.
    for _ in range(5):
        code = _new_join_code()
        if not Household.objects.filter(join_code=code).exists():
            household.join_code = code
            household.save(update_fields=["join_code"])
            return Response(_household_data(household, request))
    return Response(
        {"detail": "Code-Erzeugung fehlgeschlagen, bitte erneut versuchen."},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def household_join(request):
    """Stellt mit einem Beitritts-Code eine Beitrittsanfrage (ziel-initiiert).

    Erzeugt KEINE Mitgliedschaft – nur eine offene Anfrage, die der Owner
    bestätigen muss. So entsteht Sichtbarkeit erst mit beidseitiger Zustimmung.
    """
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
    code = str(request.data.get("code", "")).strip()
    if not code:
        return Response(
            {"detail": "Feld 'code' ist erforderlich."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    household = Household.objects.filter(join_code=code).first()
    if household is None:
        return Response(
            {"detail": "Ungültiger Beitritts-Code."},
            status=status.HTTP_404_NOT_FOUND,
        )
    join_request, created = HouseholdJoinRequest.objects.get_or_create(
        household=household,
        user=request.user,
        status=JoinRequestStatus.PENDING,
    )
    return Response(
        HouseholdJoinRequestSerializer(join_request).data,
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def household_requests(request, pk):
    """Owner listet die offenen Beitrittsanfragen seines Haushalts."""
    household = _owner_household_or_404(request.user, pk)
    qs = (
        household.join_requests.filter(status=JoinRequestStatus.PENDING)
        .select_related("user")
        .order_by("created_at")
    )
    return Response(HouseholdJoinRequestSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def household_request_decide(request, pk, req_id):
    """Owner bestätigt/lehnt eine Beitrittsanfrage ab (Body: ``decision``).

    ``decision`` = ``approve`` fügt das Mitglied hinzu (Invariante „ein Haushalt"
    wird zum Bestätigungszeitpunkt erneut geprüft), ``reject`` lehnt ab.
    """
    household = _owner_household_or_404(request.user, pk)
    join_request = (
        household.join_requests.filter(pk=req_id, status=JoinRequestStatus.PENDING)
        .select_related("user")
        .first()
    )
    if join_request is None:
        raise NotFound("Offene Anfrage nicht gefunden.")

    decision = str(request.data.get("decision", "")).strip().lower()
    if decision not in ("approve", "reject"):
        return Response(
            {"detail": "Feld 'decision' muss 'approve' oder 'reject' sein."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    target = join_request.user
    if decision == "approve" and target.households.exists():
        # Der Nutzer ist inzwischen anderswo beigetreten → Anfrage verfällt.
        join_request.status = JoinRequestStatus.REJECTED
        join_request.decided_at = timezone.now()
        join_request.decided_by = request.user
        join_request.save(update_fields=["status", "decided_at", "decided_by"])
        return Response(
            {"detail": "Nutzer ist inzwischen Mitglied eines anderen Haushalts."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        if decision == "approve":
            household.members.add(target)
            join_request.status = JoinRequestStatus.APPROVED
        else:
            join_request.status = JoinRequestStatus.REJECTED
        join_request.decided_at = timezone.now()
        join_request.decided_by = request.user
        join_request.save(update_fields=["status", "decided_at", "decided_by"])

    return Response(_household_data(household, request))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def household_leave(request, pk):
    """Verlässt den eigenen Haushalt.

    Ist er danach leer, wird er gelöscht. Verlässt der Owner, geht die
    Admin-Rolle an das dienstälteste verbleibende Mitglied über (stabile Wahl),
    damit der Haushalt nicht führungslos zurückbleibt.
    """
    household = _member_household_or_404(request.user, pk)
    household.members.remove(request.user)
    remaining = household.members.order_by("pk").first()
    if remaining is None:
        household.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if household.owner_id == request.user.id:
        household.owner = remaining
        household.save(update_fields=["owner"])
    return Response(status=status.HTTP_204_NO_CONTENT)
