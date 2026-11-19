from django.urls import path

from scheduling.views import (
    RoomDetailView,
    RoomListCreateView,
    ScheduleView,
    ScheduleExceptionView,
    MyAttendanceView,
    SessionAttendanceView,
    WeeklySlotDetailView,
    WeeklySlotListCreateView,
)

urlpatterns = [
    path('schedule/', ScheduleView.as_view(), name='schedule-list'),
    path('me/attendance/', MyAttendanceView.as_view(), name='my-attendance'),
    path(
        'sessions/<int:pk>/attendance/',
        SessionAttendanceView.as_view(),
        name='session-attendance',
    ),
    path(
        'admin/lesson-sessions/<int:pk>/exception/',
        ScheduleExceptionView.as_view(),
        name='lesson-session-exception',
    ),
    path('admin/rooms/', RoomListCreateView.as_view(), name='room-list-create'),
    path('admin/rooms/<int:pk>/', RoomDetailView.as_view(), name='room-detail'),
    path(
        'admin/weekly-slots/',
        WeeklySlotListCreateView.as_view(),
        name='weekly-slot-list-create',
    ),
    path(
        'admin/weekly-slots/<int:pk>/',
        WeeklySlotDetailView.as_view(),
        name='weekly-slot-detail',
    ),
]
