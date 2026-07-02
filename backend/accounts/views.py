from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .serializers import UserSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    """Gibt das Profil des angemeldeten Nutzers zurück."""
    return Response(UserSerializer(request.user).data)
