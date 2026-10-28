from django.db.models import Prefetch
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view, extend_schema
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    IsAuthenticated,
)

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.models import (
    CourseOffering,
    MembershipStatus,
    TeachingAssignment,
)
from academics.serializers import CourseReadSerializer
from root.openapi import common_error_responses


def pupil_course_scope(user, *, active_only=False):
    filters = {
        "archived_at__isnull": True,
        "pupil_enrollments__pupil": user,
    }
    if active_only:
        filters.update(
            {
                "pupil_enrollments__status": MembershipStatus.ACTIVE,
                "pupil_enrollments__end_date__isnull": True,
            }
        )
    return CourseOffering.objects.filter(**filters).distinct()


def teacher_course_scope(user, *, active_only=False, can_grade=False):
    filters = {
        "archived_at__isnull": True,
        "teaching_assignments__teacher": user,
    }
    if active_only:
        filters["teaching_assignments__end_date__isnull"] = True
    if can_grade:
        filters["teaching_assignments__can_grade"] = True
    return CourseOffering.objects.filter(**filters).distinct()


class IsCoursePupil(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.role != User.Role.PUPIL:
            return False
        return pupil_course_scope(
            request.user,
            active_only=request.method not in SAFE_METHODS,
        ).filter(pk=obj.pk).exists()


class IsCourseTeacher(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.role != User.Role.TEACHER:
            return False
        write_request = request.method not in SAFE_METHODS
        return teacher_course_scope(
            request.user,
            active_only=write_request,
            can_grade=write_request,
        ).filter(pk=obj.pk).exists()


class IsCourseParticipant(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.role == User.Role.PUPIL:
            return IsCoursePupil().has_object_permission(request, view, obj)
        if request.user.role == User.Role.TEACHER:
            return IsCourseTeacher().has_object_permission(request, view, obj)
        return False


class CourseReadQuerysetMixin:
    serializer_class = CourseReadSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCourseParticipant,
    )

    def get_queryset(self):
        user = self.request.user
        queryset = (
            CourseOffering.objects.filter(archived_at__isnull=True)
            .select_related(
                "subject",
                "school_class",
                "school_class__year",
            )
            .prefetch_related(
                Prefetch(
                    "teaching_assignments",
                    queryset=TeachingAssignment.objects.filter(
                        end_date__isnull=True
                    )
                    .select_related("teacher")
                    .order_by("start_date", "pk"),
                    to_attr="active_assignments",
                )
            )
        )

        if user.role == User.Role.PUPIL:
            queryset = queryset.filter(
                pk__in=pupil_course_scope(user).values("pk"),
            )
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(
                pk__in=teacher_course_scope(user).values("pk"),
            )
        else:
            return queryset.none()

        return queryset.distinct()


@extend_schema_view(
    get=extend_schema(
        summary='Kurslar ro‘yxati',
        description=(
            'Faqat session orqali kirgan teacher yoki pupil qatnashadigan '
            'arxivlanmagan kurslarni ko‘rsatadi.'
        ),
        tags=['Academics'],
        responses={
            200: CourseReadSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class CourseListView(CourseReadQuerysetMixin, ListAPIView):
    pass


@extend_schema_view(
    get=extend_schema(
        summary='Kurs tafsiloti',
        description='Joriy foydalanuvchiga tegishli bitta kursni ko‘rsatadi.',
        tags=['Academics'],
        parameters=[
            OpenApiParameter(
                name='id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description='Kurs IDsi.',
            )
        ],
        responses={
            200: CourseReadSerializer,
            **common_error_responses(),
        },
    ),
)
class CourseDetailView(CourseReadQuerysetMixin, RetrieveAPIView):
    pass
