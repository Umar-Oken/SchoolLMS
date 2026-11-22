from rest_framework import serializers

from notifications.models import Notification


class NotificationReadSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = (
            'id',
            'kind',
            'announcement',
            'assignment',
            'deadline_version',
            'title',
            'body',
            'created_at',
            'read_at',
            'is_read',
        )
        read_only_fields = fields
