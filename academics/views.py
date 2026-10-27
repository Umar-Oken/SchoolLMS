from django.db.models import Prefetch
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


class CourseListView(CourseReadQuerysetMixin, ListAPIView):
    pass


class CourseDetailView(CourseReadQuerysetMixin, RetrieveAPIView):
    pass
