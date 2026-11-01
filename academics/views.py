from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view, extend_schema
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveAPIView,
    UpdateAPIView,
)
from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    IsAuthenticated,
)

from accounts.models import User
from accounts.permissions import CanManageAcademics, MustChangePasswordPermission
from academics.models import (
    AcademicYear,
    CourseOffering,
    MembershipStatus,
    SchoolClass,
    Subject,
    TeachingAssignment,
)
from academics.serializers import (
    AcademicYearAggregateSerializer,
    AcademicYearCatalogReadSerializer,
    CourseOfferingWriteSerializer,
    CourseReadSerializer,
    SchoolClassOptionSerializer,
    SubjectOptionSerializer,
)
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


class CourseOfferingCreateView(CreateAPIView):
    queryset = CourseOffering.objects.all()
    serializer_class = CourseOfferingWriteSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )


class CourseOfferingUpdateView(UpdateAPIView):
    serializer_class = CourseOfferingWriteSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )

    def get_queryset(self):
        return CourseOffering.objects.select_for_update()

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            return super().update(request, *args, **kwargs)


class AcademicYearCatalogListView(ListAPIView):
    serializer_class = AcademicYearCatalogReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get_queryset(self):
        return AcademicYear.objects.prefetch_related("terms")


class AcademicYearCatalogDetailView(RetrieveAPIView):
    serializer_class = AcademicYearCatalogReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)
    queryset = AcademicYear.objects.prefetch_related("terms")


class AcademicYearAggregateCreateView(CreateAPIView):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearAggregateSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )


class AcademicYearAggregateUpdateView(UpdateAPIView):
    serializer_class = AcademicYearAggregateSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )

    def get_queryset(self):
        return AcademicYear.objects.select_for_update().prefetch_related("terms")

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            return super().update(request, *args, **kwargs)


class SchoolClassOptionListView(ListAPIView):
    queryset = SchoolClass.objects.select_related("year")
    serializer_class = SchoolClassOptionSerializer


class AvailableSubjectOptionListView(ListAPIView):
    serializer_class = SubjectOptionSerializer

    def get_queryset(self):
        school_class = get_object_or_404(
            SchoolClass,
            pk=self.kwargs["school_class_id"],
        )
        used_subjects = CourseOffering.objects.filter(
            school_class=school_class,
        ).values("subject_id")
        return Subject.objects.exclude(pk__in=used_subjects)
