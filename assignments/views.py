from django.db import transaction
from django.db.models import QuerySet
from rest_framework import generics
from rest_framework.permissions import BasePermission, IsAuthenticated
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.views import teacher_course_scope
from assignments.models import Assignment
from assignments.serializers import AssignmentReadSerializer, AssignmentSerializer
from root.openapi import common_error_responses


class IsTeacher(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'role', None) == User.Role.TEACHER


@extend_schema_view(
    post=extend_schema(
        summary='Draft vazifa yaratish',
        description=(
            'Faol ravishda biriktirilgan kursdagi o‘z mavzusiga draft vazifa '
            'yaratadi. State server tomonidan draft qilib belgilanadi.'
        ),
        tags=['Assignments'],
        request=AssignmentSerializer,
        responses={
            201: AssignmentReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Draft vazifa yaratish',
                value={
                    'topic': 42,
                    'title': 'Kasrlarni taqqoslash',
                    'instructions': 'Javobni tushuntirib yozing.',
                    'available_at': '2026-11-10T09:00:00Z',
                    'due_at': '2026-11-14T12:00:00Z',
                    'closes_at': '2026-11-15T12:00:00Z',
                    'max_score': '10.00',
                    'max_attempts': 2,
                },
                request_only=True,
            ),
            OpenApiExample(
                'Yaratilgan draft vazifa',
                value={
                    'id': 1,
                    'topic': 42,
                    'title': 'Kasrlarni taqqoslash',
                    'instructions': 'Javobni tushuntirib yozing.',
                    'available_at': '2026-11-10T09:00:00Z',
                    'due_at': '2026-11-14T12:00:00Z',
                    'closes_at': '2026-11-15T12:00:00Z',
                    'max_score': '10.00',
                    'max_attempts': 2,
                    'state': 'draft',
                },
                response_only=True,
            ),
        ],
    ),
)
class AssignmentCreateView(generics.CreateAPIView):
    serializer_class = AssignmentSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )
    queryset = Assignment.objects.none()

    def perform_create(self, serializer):
        serializer.save(state=Assignment.State.DRAFT)


@extend_schema_view(
    get=extend_schema(
        summary='Draft vazifa tafsiloti',
        description=(
            'Faol ravishda tegishli kursga biriktirilgan teacher o‘z draft '
            'vazifasini ko‘radi. Pupil va scope’dan tashqari vazifa ko‘rinmaydi.'
        ),
        tags=['Assignments'],
        responses={
            200: AssignmentReadSerializer,
            **common_error_responses(),
        },
    ),
)
class AssignmentDetailView(generics.RetrieveAPIView):
    serializer_class = AssignmentReadSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self) -> QuerySet[Assignment]:
        user = self.request.user
        if user.role != User.Role.TEACHER:
            return Assignment.objects.none()
        courses = teacher_course_scope(user, active_only=True)
        return Assignment.objects.filter(
            topic__course_id__in=courses.values('pk'),
            topic__archived_at__isnull=True,
            state=Assignment.State.DRAFT,
        ).select_related('topic')
