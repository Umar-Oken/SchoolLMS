from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from rest_framework.decorators import action
from rest_framework import mixins, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.models import CourseOffering
from academics.views import pupil_course_scope, teacher_course_scope
from learning.models import Topic
from learning.serializers import (
    TopicCreateSerializer,
    TopicReadSerializer,
    TopicUpdateSerializer,
)
from learning.services import publish_topic
from root.openapi import CSRF_HEADER, common_error_responses


class IsTopicWriteTeacher(BasePermission):
    def has_permission(self, request, view):
        return (
            request.method in ('GET', 'HEAD', 'OPTIONS')
            or request.user.role == User.Role.TEACHER
        )


@extend_schema_view(
    list=extend_schema(
        summary='Kurs mavzulari ro‘yxati',
        description=(
            'Teacher o‘z kursining draft va published mavzularini, pupil esa '
            'faqat o‘z kursidagi published mavzularni ko‘radi. Arxivlanganlar berilmaydi.'
        ),
        tags=['Learning'],
        responses={
            200: TopicReadSerializer(many=True),
            **common_error_responses(),
        },
    ),
    retrieve=extend_schema(
        summary='Mavzu tafsiloti',
        description=(
            'Teacher o‘z mavzusini, pupil esa faqat published mavzuni ko‘radi. '
            'Ko‘rinmaydigan draft IDsi ham 404 qaytaradi.'
        ),
        tags=['Learning'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Mavzu IDsi.',
            )
        ],
        responses={
            200: TopicReadSerializer,
            **common_error_responses(),
        },
    ),
    create=extend_schema(
        summary='Draft mavzu yaratish',
        description=(
            'Faqat biriktirilgan course uchun draft yaratadi. State va '
            'published_at server tomonidan boshqariladi; term course yiliga mos bo‘lishi kerak.'
        ),
        tags=['Learning'],
        parameters=[CSRF_HEADER],
        request=TopicCreateSerializer,
        responses={
            201: TopicReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Draft yaratish',
                value={
                    'course': 12,
                    'term': 3,
                    'title': 'Kasrlar',
                    'objective': 'Oddiy kasrlarni taqqoslash',
                    'order': 1,
                },
                request_only=True,
            )
        ],
    ),
    partial_update=extend_schema(
        summary='Draft mavzuni tahrirlash',
        description=(
            'Faqat teacherning draft mavzusini qisman yangilaydi. Course, '
            'state va published_at o‘zgartirilmaydi; PUT va DELETE yo‘q.'
        ),
        tags=['Learning'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Tahrirlanadigan draft mavzu IDsi.',
            ),
            CSRF_HEADER,
        ],
        request=TopicUpdateSerializer,
        responses={
            200: TopicReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Draftni qisman o‘zgartirish',
                value={'title': 'Kasrlarni taqqoslash'},
                request_only=True,
            )
        ],
    ),
)
class TopicViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    serializer_class = TopicReadSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTopicWriteTeacher,
    )

    def get_serializer_class(self):
        if self.action == 'create':
            return TopicCreateSerializer
        if self.action == 'partial_update':
            return TopicUpdateSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        serializer.save(state=Topic.State.DRAFT, published_at=None)

    @extend_schema(
        summary='Mavzuni arxivlash',
        description=(
            'Teacher mavzuni arxivlaydi. Qator va unga bog‘liq tarix o‘chmaydi, '
            'ammo mavzu faol ro‘yxatlardan yashiriladi.'
        ),
        tags=['Learning'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Arxivlanadigan mavzu IDsi.',
            ),
            CSRF_HEADER,
        ],
        request=None,
        responses={
            200: TopicReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Arxivlangan mavzu',
                value={
                    'id': 42,
                    'course': 12,
                    'term': 3,
                    'title': 'Kasrlar',
                    'objective': 'Oddiy kasrlarni taqqoslash',
                    'order': 1,
                    'state': 'draft',
                    'published_at': None,
                    'archived_at': '2026-03-19T10:00:00Z',
                },
                response_only=True,
            )
        ],
    )
    @action(detail=True, methods=['post'])
    def archive(self, request, *args, **kwargs):
        topic = self.get_object()
        topic.archived_at = timezone.now()
        topic.save(update_fields=['archived_at'])
        return Response(
            TopicReadSerializer(topic).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary='Mavzuni nashr qilish',
        description=(
            'Assigned teacher to‘ldirilgan draftni nashr qiladi. Takroriy '
            'chaqiruv yangi yozuv yaratmaydi; nashr vaqti saqlanadi.'
        ),
        tags=['Learning'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Nashr qilinadigan draft mavzu IDsi.',
            ),
            CSRF_HEADER,
        ],
        request=None,
        responses={
            200: TopicReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Nashr qilingan mavzu',
                value={
                    'id': 42,
                    'course': 12,
                    'term': 3,
                    'title': 'Kasrlar',
                    'objective': 'Oddiy kasrlarni taqqoslash',
                    'order': 1,
                    'state': 'published',
                    'published_at': '2026-03-19T10:00:00Z',
                    'archived_at': None,
                },
                response_only=True,
            )
        ],
    )
    @action(detail=True, methods=['post'])
    def publish(self, request, *args, **kwargs):
        topic = publish_topic(self.kwargs['pk'], actor=request.user)
        return Response(TopicReadSerializer(topic).data)

    def _course_scope(self):
        user = self.request.user
        if not isinstance(user, User):
            return CourseOffering.objects.none()
        if user.role == User.Role.PUPIL:
            return pupil_course_scope(user)
        if user.role == User.Role.TEACHER:
            return teacher_course_scope(user)
        return CourseOffering.objects.none()

    def get_queryset(self):
        user = self.request.user
        if not isinstance(user, User):
            return Topic.objects.none()

        course_scope = self._course_scope()
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            course_scope = teacher_course_scope(user, active_only=True)

        queryset = Topic.objects.select_related(
            'course',
            'term',
            'term__year',
            'course__school_class',
            'course__school_class__year',
        ).filter(
            course_id__in=course_scope.values('pk'),
            archived_at__isnull=True,
        )

        if user.role == User.Role.PUPIL:
            queryset = queryset.filter(state=Topic.State.PUBLISHED)

        if self.action == 'partial_update':
            queryset = queryset.filter(state=Topic.State.DRAFT)

        course_pk = self.kwargs.get('course_pk')
        if course_pk is not None:
            queryset = queryset.filter(course_id=course_pk)

        return queryset

    def list(self, request, *args, **kwargs):
        course_pk = kwargs.get('course_pk')
        if (
            course_pk is not None
            and not self._course_scope().filter(pk=course_pk).exists()
        ):
            raise NotFound()
        return super().list(request, *args, **kwargs)
