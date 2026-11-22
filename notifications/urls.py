from django.urls import path

from notifications.views import NotificationListView, NotificationReadView

urlpatterns = [
    path(
        'notifications/',
        NotificationListView.as_view(),
        name='notification-list',
    ),
    path(
        'notifications/<int:pk>/read/',
        NotificationReadView.as_view(),
        name='notification-read',
    ),
]
