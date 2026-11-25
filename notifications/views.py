from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.filters import OrderingFilter
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import MustChangePasswordPermission
from notifications.models import Notification
from notifications.filters import NotificationFilter
from notifications.pagination import NotificationPagination
from notifications.serializers import NotificationReadSerializer
from root.openapi import common_error_responses
from django_filters.rest_framework import DjangoFilterBackend


class NotificationListView(generics.ListAPIView):
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
    )
    serializer_class = NotificationReadSerializer
    pagination_class = NotificationPagination
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = NotificationFilter
    ordering_fields = ('created_at', 'title')
    ordering = ('-created_at', '-pk')

    def get_queryset(self):
        return Notification.objects.filter(
            recipient=self.request.user,
            cancelled_at__isnull=True,
        ).select_related('announcement', 'assignment')

    @extend_schema(
        summary='Bildirishnomalar ro‘yxati',
        description='Joriy foydalanuvchining sahifalangan bildirishnomalari.',
        tags=['Notifications'],
        responses=common_error_responses(),
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class NotificationReadView(generics.GenericAPIView):
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
    )
    serializer_class = NotificationReadSerializer

    def get_queryset(self):
        return Notification.objects.filter(
            recipient=self.request.user,
            cancelled_at__isnull=True,
        ).select_related('announcement', 'assignment')

    @extend_schema(
        summary='Bildirishnomani o‘qilgan deb belgilash',
        description=(
            'Joriy foydalanuvchining bildirishnomasiga o‘qilgan vaqtini '
            'idempotent tarzda yozadi.'
        ),
        tags=['Notifications'],
        request=None,
        responses={
            200: NotificationReadSerializer,
            **common_error_responses(),
        },
    )
    def patch(self, request, *args, **kwargs):
        notification = self.get_queryset().filter(pk=kwargs['pk']).first()
        if notification is None:
            raise NotFound()
        self.get_queryset().filter(
            pk=notification.pk,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        notification = self.get_queryset().get(pk=notification.pk)
        return Response(
            self.get_serializer(notification).data,
            status=status.HTTP_200_OK,
        )
