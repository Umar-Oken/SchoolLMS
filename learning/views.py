from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from rest_framework.decorators import action
from rest_framework import generics, mixins, status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
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
from academics.services import ensure_term_open
from academics.views import pupil_course_scope, teacher_course_scope
from files.models import Attachment
from learning.models import Completion, Resource, Topic
from learning.serializers import (
    CompletionMarkSerializer,
    CompletionReadSerializer,
    ResourceCreateSerializer,
    ResourceReadSerializer,
    TopicDetailReadSerializer,
    TopicCreateSerializer,
    TopicReadSerializer,
    TopicUpdateSerializer,
    validate_clean_pdf_attachment,
)
from learning.services import publish_resource, publish_topic
from root.openapi import common_error_responses


class IsTopicWriteTeacher(BasePermission):
    def has_permission(self, request, view):
        return (
            request.method in ('GET', 'HEAD', 'OPTIONS')
            or request.user.role == User.Role.TEACHER
        )


class IsResourceWriterOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return (
            request.method in ('GET', 'HEAD', 'OPTIONS')
            or getattr(request.user, 'role', None) == User.Role.TEACHER
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
            200: TopicDetailReadSerializer,
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
        if self.action == 'retrieve':
            return TopicDetailReadSerializer
        return super().get_serializer_class()

    def perform_create(self, serializer):
        with transaction.atomic():
            ensure_term_open(serializer.validated_data['term'], lock=True)
            serializer.save(state=Topic.State.DRAFT, published_at=None)

    def perform_update(self, serializer):
        with transaction.atomic():
            term = serializer.validated_data.get(
                'term',
                serializer.instance.term,
            )
            ensure_term_open(term, lock=True)
            serializer.save()

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


class TopicCompletionView(generics.GenericAPIView):
    serializer_class = CompletionMarkSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
    )

    def get_queryset(self):
        user = self.request.user
        if user.role != User.Role.PUPIL:
            return Topic.objects.none()
        active_courses = pupil_course_scope(
            user,
            active_only=True,
        ).filter(
            pupil_enrollments__start_date__lte=timezone.localdate(),
        )
        return Topic.objects.filter(
            course_id__in=active_courses.values('pk'),
            state=Topic.State.PUBLISHED,
            archived_at__isnull=True,
        )

    @extend_schema(
        summary='Mavzuni o‘qilgan deb belgilash',
        description=(
            'Joriy pupil uchun published mavzuda idempotent read completion '
            'yaratadi. Bu belgi bilim mastery yoki baho hisoblanmaydi.'
        ),
        tags=['Learning'],
        request=CompletionMarkSerializer,
        responses={
            200: CompletionReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Completion so‘rovi',
                value={},
                request_only=True,
            )
        ],
    )
    def put(self, request, *args, **kwargs):
        if request.user.role != User.Role.PUPIL:
            raise PermissionDenied(
                'Mavzu completion belgisini faqat pupil qo‘yishi mumkin.'
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        completion, _ = Completion.objects.get_or_create(
            pupil=request.user,
            topic=topic,
            kind=Completion.Kind.READ,
        )
        return Response(
            CompletionReadSerializer(completion).data,
            status=status.HTTP_200_OK,
        )


def resource_topic_scope(user, *, write=False):
    if user.role == User.Role.PUPIL:
        if write:
            return Topic.objects.none()
        courses = pupil_course_scope(user)
        return Topic.objects.filter(course_id__in=courses.values('pk'))
    if user.role == User.Role.TEACHER:
        courses = teacher_course_scope(user, active_only=write)
        return Topic.objects.filter(course_id__in=courses.values('pk'))
    return Topic.objects.none()


class ResourceListCreateView(generics.ListCreateAPIView):
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsResourceWriterOrReadOnly,
    )
    serializer_class = ResourceReadSerializer

    def get_topic(self):
        topics = resource_topic_scope(
            self.request.user,
            write=self.request.method == 'POST',
        ).filter(archived_at__isnull=True)
        if self.request.user.role == User.Role.PUPIL:
            topics = topics.filter(state=Topic.State.PUBLISHED)
        return get_object_or_404(topics, pk=self.kwargs['topic_pk'])

    def get_queryset(self):
        topic = self.get_topic()
        queryset = Resource.objects.filter(topic=topic).select_related(
            'topic',
            'attachment',
        )
        if self.request.user.role == User.Role.PUPIL:
            queryset = queryset.filter(
                state=Resource.State.PUBLISHED,
            ).filter(
                Q(
                    kind=Resource.Kind.PDF,
                    attachment__scan_status=Attachment.ScanStatus.CLEAN,
                    attachment__detected_mime='application/pdf',
                )
                | Q(kind__in=[Resource.Kind.VIDEO, Resource.Kind.LINK])
            )
        return queryset

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ResourceCreateSerializer
        return ResourceReadSerializer

    def create(self, request, *args, **kwargs):
        self.resource_topic = self.get_topic()
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        with transaction.atomic():
            attachment = serializer.validated_data.get('attachment')
            if attachment is not None:
                try:
                    attachment = Attachment.objects.select_for_update().get(
                        pk=attachment.pk,
                    )
                except Attachment.DoesNotExist as exc:
                    raise NotFound() from exc
                validate_clean_pdf_attachment(attachment, self.request.user)

            serializer.save(
                topic=self.resource_topic,
                attachment=attachment,
                state=Resource.State.DRAFT,
                version=1,
            )

    @extend_schema(
        summary='Mavzu resurslari',
        description=(
            'Teacher o‘z biriktirilgan mavzusidagi barcha resurslarni, pupil esa '
            'faqat o‘z published mavzusidagi published resurslarni ko‘radi. '
            'PDF uchun clean attachment, video/link uchun HTTPS provider URL kerak.'
        ),
        tags=['Learning'],
        request=ResourceCreateSerializer,
        responses={
            200: ResourceReadSerializer(many=True),
            201: ResourceReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'PDF resurs yaratish',
                value={
                    'kind': 'pdf',
                    'title': 'Kasrlar bo‘yicha material',
                    'description': 'Dars uchun PDF.',
                    'transcript_text': '',
                    'attachment': 17,
                },
                request_only=True,
            )
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary='Draft PDF resurs biriktirish',
        description=(
            'Faol biriktirilgan teacher o‘z mavzusiga PDF, video yoki link '
            'resursini biriktiradi. PDF uchun o‘zining clean Attachment ID’si, '
            'video/link uchun ruxsatli HTTPS URL talab qilinadi. State/version '
            'server tomonidan boshqariladi; URL serverdan olinmaydi.'
        ),
        tags=['Learning'],
        request=ResourceCreateSerializer,
        responses={
            201: ResourceReadSerializer,
            **common_error_responses(),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ResourceDetailView(generics.RetrieveAPIView):
    serializer_class = ResourceReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get_queryset(self):
        user = self.request.user
        topics = resource_topic_scope(user)
        queryset = Resource.objects.filter(
            topic__in=topics,
            topic__archived_at__isnull=True,
        ).select_related('topic', 'attachment')
        if user.role == User.Role.PUPIL:
            queryset = queryset.filter(
                topic__state=Topic.State.PUBLISHED,
                state=Resource.State.PUBLISHED,
            ).filter(
                Q(
                    kind=Resource.Kind.PDF,
                    attachment__scan_status=Attachment.ScanStatus.CLEAN,
                    attachment__detected_mime='application/pdf',
                )
                | Q(kind__in=[Resource.Kind.VIDEO, Resource.Kind.LINK])
            )
        return queryset


class ResourcePublishView(generics.GenericAPIView):
    serializer_class = ResourceReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get_queryset(self):
        topics = resource_topic_scope(
            self.request.user,
            write=True,
        ).filter(archived_at__isnull=True)
        return Resource.objects.filter(topic__in=topics).select_related(
            'topic',
            'attachment',
        )

    @extend_schema(
        summary='Draft resursni nashr qilish',
        description=(
            'Faol biriktirilgan teacher faqat published mavzudagi draft resursni '
            'nashr qiladi. PDF attachment clean bo‘lishi shart; video/link URL '
            'allowlist bo‘yicha avval tekshiriladi. State request body orqali '
            'o‘zgartirilmaydi.'
        ),
        tags=['Learning'],
        request=None,
        responses={
            200: ResourceReadSerializer,
            **common_error_responses(),
        },
    )
    def post(self, request, *args, **kwargs):
        self.get_object()
        resource = publish_resource(
            self.kwargs['pk'],
            actor=request.user,
        )
        return Response(ResourceReadSerializer(resource).data)
