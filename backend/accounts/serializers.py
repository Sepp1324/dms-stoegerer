from rest_framework import serializers

from .models import User


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
