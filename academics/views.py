from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    GenericAPIView,
    ListCreateAPIView,
    ListAPIView,
    RetrieveAPIView,
    RetrieveUpdateAPIView,
    UpdateAPIView,
)
from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    IsAuthenticated,
)
from rest_framework.response import Response

from accounts.models import User
from accounts.permissions import CanManageAcademics, MustChangePasswordPermission
from academics.models import (
    AcademicYear,
    CourseOffering,
    Enrollment,
    MembershipStatus,
    SchoolClass,
    Subject,
    TeachingAssignment,
)
from academics.serializers import (
    AcademicYearAggregateSerializer,
    AcademicYearCatalogReadSerializer,
    ClassMembershipCommandSerializer,
    ClassMembershipReadSerializer,
    CourseEnrollmentSyncCommandSerializer,
    CourseEnrollmentSyncResultSerializer,
    CoursePupilRosterSerializer,
    CourseOfferingWriteSerializer,
    CourseReadSerializer,
    SchoolClassCatalogSerializer,
    SchoolClassOptionSerializer,
    SubjectAdminSerializer,
    SubjectCatalogSerializer,
    TeacherOptionSerializer,
    TeachingAssignmentCloseCommandSerializer,
    TeachingAssignmentCommandSerializer,
    TeachingAssignmentReadSerializer,
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
    today = timezone.localdate()
    assignments = TeachingAssignment.objects.filter(teacher=user)
    if active_only:
        assignments = assignments.filter(
            start_date__lte=today,
        ).filter(
            Q(end_date__gte=today) | Q(end_date__isnull=True),
        )
    if can_grade:
        assignments = assignments.filter(can_grade=True)
    return CourseOffering.objects.filter(
        archived_at__isnull=True,
        pk__in=assignments.values("course_id"),
    )


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
                        start_date__lte=timezone.localdate(),
                    )
                    .filter(
                        Q(end_date__gte=timezone.localdate())
                        | Q(end_date__isnull=True)
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


def course_roster_scope(user):
    courses = CourseOffering.objects.filter(archived_at__isnull=True)
    if user.is_superuser or (
        user.role == User.Role.TEACHER
        and user.has_perm("accounts.manage_academics")
    ):
        return courses
    if user.role == User.Role.TEACHER:
        return teacher_course_scope(user, active_only=True)
    return courses.none()


@extend_schema(
    summary="Kursning faol o‘quvchilar ro‘yxati",
    description=(
        "Faol biriktirilgan teacher yoki manage_academics operator faqat "
        "kursning faol enrollment va membership’li o‘quvchilarini ko‘radi. "
        "Javobda faqat pupil ID, ism va familiya bor."
    ),
    tags=["Academics"],
    responses={
        200: CoursePupilRosterSerializer(many=True),
        **common_error_responses(),
    },
)
class CoursePupilRosterView(ListAPIView):
    serializer_class = CoursePupilRosterSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
    )

    def get_course(self):
        return get_object_or_404(
            course_roster_scope(self.request.user),
            pk=self.kwargs["course_pk"],
        )

    def get_queryset(self):
        course = self.get_course()
        today = timezone.localdate()
        return (
            Enrollment.objects.filter(
                course=course,
                status=MembershipStatus.ACTIVE,
                start_date__lte=today,
                end_date__isnull=True,
                pupil__class_memberships__school_class_id=course.school_class_id,
                pupil__class_memberships__status=MembershipStatus.ACTIVE,
                pupil__class_memberships__start_date__lte=today,
                pupil__class_memberships__end_date__isnull=True,
            )
            .select_related("pupil")
            .order_by("pupil__last_name", "pupil__first_name", "pupil_id")
        )


class CanSyncCourseEnrollments(CanManageAcademics):
    def has_permission(self, request, view):
        return (
            request.user.is_superuser
            or (
                request.user.role == User.Role.TEACHER
                and super().has_permission(request, view)
            )
        )


@extend_schema(
    summary="Kurs enrollmentlarini sinxronlash",
    description=(
        "manage_academics operator faol sinf a’zolari uchun yetishmagan "
        "enrollmentlarni ochadi. Takroriy so‘rov xavfsiz; mavjud, jumladan "
        "yopilgan enrollment tarixi o‘zgarmaydi."
    ),
    tags=["Academics"],
    request=CourseEnrollmentSyncCommandSerializer,
    responses={
        200: CourseEnrollmentSyncResultSerializer,
        **common_error_responses(),
    },
    examples=[
        OpenApiExample(
            "Bo‘sh command",
            value={},
            request_only=True,
        ),
    ],
)
class CourseEnrollmentSyncView(GenericAPIView):
    serializer_class = CourseEnrollmentSyncCommandSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanSyncCourseEnrollments,
    )

    def post(self, request, *args, **kwargs):
        course = get_object_or_404(
            CourseOffering.objects.filter(archived_at__isnull=True),
            pk=self.kwargs["course_pk"],
        )
        serializer = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "course": course},
        )
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(
            CourseEnrollmentSyncResultSerializer(result).data,
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Course katalog elementi yaratish",
        description=(
            "manage_academics huquqli operator sinf va faol fanni "
            "biriktiradi; bir sinfda fan takrorlanmaydi."
        ),
        tags=["Academics"],
        request=CourseOfferingWriteSerializer,
        responses={
            201: CourseOfferingWriteSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Course yaratish",
                value={"school_class": 1, "subject": 2},
                request_only=True,
            ),
        ],
    ),
)
class CourseOfferingCreateView(CreateAPIView):
    queryset = CourseOffering.objects.all()
    serializer_class = CourseOfferingWriteSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )


@extend_schema_view(
    put=extend_schema(
        summary="Course katalog elementini to‘liq yangilash",
        description=(
            "manage_academics huquqi kerak. Tarixga ega course’ning sinf yoki "
            "fanini almashtirib bo‘lmaydi."
        ),
        tags=["Academics"],
        request=CourseOfferingWriteSerializer,
        responses={
            200: CourseOfferingWriteSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Course ma’lumotlari",
                value={"school_class": 1, "subject": 2},
                request_only=True,
            ),
        ],
    ),
    patch=extend_schema(
        summary="Course katalog elementini qisman yangilash",
        description=(
            "manage_academics huquqi kerak. Faqat o‘zgaradigan sinf/fan ID’sini "
            "yuboring; tarixga ega course’ning ota ma’lumotlari o‘zgarmaydi."
        ),
        tags=["Academics"],
        request=CourseOfferingWriteSerializer,
        responses={
            200: CourseOfferingWriteSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Course fanini yangilash",
                value={"subject": 3},
                request_only=True,
            ),
        ],
    ),
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


@extend_schema_view(
    get=extend_schema(
        summary="O‘quv yillari katalogi",
        description="Teacher va pupil uchun yil hamda choraklarning read-only ro‘yxati.",
        tags=["Academics"],
        responses={
            200: AcademicYearCatalogReadSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class AcademicYearCatalogListView(ListAPIView):
    serializer_class = AcademicYearCatalogReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get_queryset(self):
        return AcademicYear.objects.prefetch_related("terms")


@extend_schema_view(
    get=extend_schema(
        summary="O‘quv yili tafsiloti",
        description="Bitta o‘quv yilini uning choraklari bilan qaytaradi.",
        tags=["Academics"],
        responses={
            200: AcademicYearCatalogReadSerializer,
            **common_error_responses(),
        },
    ),
)
class AcademicYearCatalogDetailView(RetrieveAPIView):
    serializer_class = AcademicYearCatalogReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)
    queryset = AcademicYear.objects.prefetch_related("terms")


@extend_schema_view(
    post=extend_schema(
        summary="O‘quv yili va choraklarini yaratish",
        description=(
            "manage_academics huquqli operator yil va choraklarni bitta "
            "transaction’da yaratadi. Har chorak tartibi yil ichida unique."
        ),
        tags=["Academics"],
        request=AcademicYearAggregateSerializer,
        responses={
            201: AcademicYearAggregateSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "O‘quv yili va birinchi chorak",
                value={
                    "label": "2026-2027",
                    "start_date": "2026-09-01",
                    "end_date": "2027-06-30",
                    "status": "upcoming",
                    "terms": [
                        {
                            "order": 1,
                            "start_date": "2026-09-01",
                            "end_date": "2026-11-30",
                            "status": "upcoming",
                        }
                    ],
                },
                request_only=True,
            ),
        ],
    ),
)
class AcademicYearAggregateCreateView(CreateAPIView):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearAggregateSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )


@extend_schema_view(
    put=extend_schema(
        summary="O‘quv yili va choraklarini yangilash",
        description=(
            "manage_academics huquqi kerak. Mavjud chorak ID’lari shu yilga "
            "tegishli bo‘lishi shart; yuborilmagan choraklar saqlanadi."
        ),
        tags=["Academics"],
        request=AcademicYearAggregateSerializer,
        responses={
            200: AcademicYearAggregateSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "O‘quv yili va mavjud chorak",
                value={
                    "label": "2026-2027",
                    "start_date": "2026-09-01",
                    "end_date": "2027-06-30",
                    "status": "active",
                    "terms": [
                        {
                            "id": 1,
                            "order": 1,
                            "start_date": "2026-09-01",
                            "end_date": "2026-11-30",
                            "status": "active",
                        }
                    ],
                },
                request_only=True,
            ),
        ],
    ),
    patch=extend_schema(
        summary="O‘quv yili va choraklarini qisman yangilash",
        description=(
            "manage_academics huquqi kerak. Choraklar yuborilsa mavjudlari ID "
            "bilan beriladi; ro‘yxatdan tushirib qoldirish o‘chirmaydi."
        ),
        tags=["Academics"],
        request=AcademicYearAggregateSerializer,
        responses={
            200: AcademicYearAggregateSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "O‘quv yili statusini o‘zgartirish",
                value={"status": "active"},
                request_only=True,
            ),
        ],
    ),
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


class IsCatalogReadOrManager(BasePermission):
    def has_permission(self, request, view):
        if request.user.is_superuser:
            return True
        if request.method in SAFE_METHODS:
            return request.user.role in (User.Role.TEACHER, User.Role.PUPIL)
        return request.user.has_perm("accounts.manage_academics")


class CanManageTeachingAssignments(CanManageAcademics):
    def has_permission(self, request, view):
        return (
            request.user.is_superuser
            or (
                request.user.role == User.Role.TEACHER
                and super().has_permission(request, view)
            )
        )


class CanManageClassMemberships(CanManageAcademics):
    def has_permission(self, request, view):
        return (
            request.user.is_superuser
            or (
                request.user.role == User.Role.TEACHER
                and super().has_permission(request, view)
            )
        )


@extend_schema_view(
    post=extend_schema(
        summary="O‘quvchini sinfga biriktirish yoki ko‘chirish",
        description=(
            "manage_academics operatori aniq start_date bilan pupilni sinfga "
            "biriktiradi yoki ko‘chiradi. Avvalgi membership va enrollment "
            "intervallari yopiladi, yangi sinfning faol kurslariga enrollment "
            "ochiladi; tarixiy qatorlar o‘chirilmaydi. Ustma-ust sana rad etiladi."
        ),
        tags=["Academics"],
        request=ClassMembershipCommandSerializer,
        responses={
            201: ClassMembershipReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "O‘quvchini sinfga biriktirish",
                value={
                    "pupil": 24,
                    "school_class": 3,
                    "start_date": "2026-11-06",
                },
                request_only=True,
            ),
        ],
    ),
)
class ClassMembershipCreateView(CreateAPIView):
    serializer_class = ClassMembershipCommandSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageClassMemberships,
    )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(
            ClassMembershipReadSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        summary="Faol o‘qituvchilar ro‘yxati",
        description=(
            "manage_academics operatori biriktirish uchun faol teacherlarning "
            "minimal identifikatsiya profilini oladi."
        ),
        tags=["Academics"],
        responses={
            200: TeacherOptionSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class TeacherOptionListView(ListAPIView):
    serializer_class = TeacherOptionSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageTeachingAssignments,
    )

    def get_queryset(self):
        return User.objects.filter(
            role=User.Role.TEACHER,
            is_active=True,
        ).only("id", "username", "first_name", "last_name")


@extend_schema_view(
    post=extend_schema(
        summary="Kursga o‘qituvchi biriktirish",
        description=(
            "manage_academics operatori assignment yaratadi. Mavjud "
            "biriktirishlar yangi boshlanish sanasidan bir kun oldin yopiladi; "
            "tarixiy qatorlar o‘chirilmaydi. can_grade faqat serverda shu "
            "assignment uchun tekshiriladi."
        ),
        tags=["Academics"],
        request=TeachingAssignmentCommandSerializer,
        responses={
            201: TeachingAssignmentReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "O‘qituvchini kursga biriktirish",
                value={
                    "course": 12,
                    "teacher": 7,
                    "start_date": "2026-11-04",
                    "can_grade": True,
                },
                request_only=True,
            ),
        ],
    ),
)
class TeachingAssignmentCreateView(CreateAPIView):
    serializer_class = TeachingAssignmentCommandSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageTeachingAssignments,
    )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = serializer.save()
        return Response(
            TeachingAssignmentReadSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        summary="O‘qituvchi biriktirish intervalini yopish",
        description=(
            "manage_academics operatori mavjud assignment’ga end_date yozadi. "
            "Teacher va can_grade tarixi saqlanadi; yopilgan intervalni qayta "
            "tahrirlash mumkin emas."
        ),
        tags=["Academics"],
        request=TeachingAssignmentCloseCommandSerializer,
        responses={
            200: TeachingAssignmentReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Biriktirishni yopish",
                value={"end_date": "2026-11-03"},
                request_only=True,
            ),
        ],
    ),
)
class TeachingAssignmentCloseView(GenericAPIView):
    serializer_class = TeachingAssignmentCloseCommandSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageTeachingAssignments,
    )

    def get_queryset(self):
        return TeachingAssignment.objects.select_related("course", "teacher")

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        assignment = self.get_object()
        serializer = self.get_serializer(
            assignment,
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        closed_assignment = serializer.save()
        return Response(TeachingAssignmentReadSerializer(closed_assignment).data)


@extend_schema_view(
    get=extend_schema(
        summary="Sinf katalogi",
        description="Teacher va pupil uchun sinflar read-only ro‘yxati.",
        tags=["Academics"],
        responses={
            200: SchoolClassCatalogSerializer(many=True),
            **common_error_responses(),
        },
    ),
    post=extend_schema(
        summary="Sinf yaratish",
        description=(
            "manage_academics huquqi kerak. Yil, 1–11 sinf darajasi va "
            "section beriladi; bir yilda shu sinf takrorlanmaydi."
        ),
        tags=["Academics"],
        request=SchoolClassCatalogSerializer,
        responses={
            201: SchoolClassCatalogSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Sinf yaratish",
                value={"year": 1, "grade_level": 7, "section": "A"},
                request_only=True,
            ),
        ],
    ),
)
class SchoolClassCatalogListCreateView(ListCreateAPIView):
    queryset = SchoolClass.objects.select_related("year")
    serializer_class = SchoolClassCatalogSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )


@extend_schema_view(
    get=extend_schema(
        summary="Sinf tafsiloti",
        description="Bitta sinfning o‘quv yili, darajasi va section’ini qaytaradi.",
        tags=["Academics"],
        responses={
            200: SchoolClassCatalogSerializer,
            **common_error_responses(),
        },
    ),
    put=extend_schema(
        summary="Sinf katalogini to‘liq yangilash",
        description=(
            "manage_academics huquqi kerak. Tarixli sinf darajasi/section’i "
            "almashtirilmaydi va sinf boshqa yilga ko‘chirilmaydi."
        ),
        tags=["Academics"],
        request=SchoolClassCatalogSerializer,
        responses={
            200: SchoolClassCatalogSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Sinf ma’lumotlari",
                value={"year": 1, "grade_level": 7, "section": "A"},
                request_only=True,
            ),
        ],
    ),
    patch=extend_schema(
        summary="Sinf katalogini qisman yangilash",
        description=(
            "manage_academics huquqi kerak. Yangi o‘quv yili uchun eski "
            "sinfni rename qilish o‘rniga yangi sinf yarating."
        ),
        tags=["Academics"],
        request=SchoolClassCatalogSerializer,
        responses={
            200: SchoolClassCatalogSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Sinf section’ini yangilash",
                value={"section": "B"},
                request_only=True,
            ),
        ],
    ),
)
class SchoolClassCatalogRetrieveUpdateView(RetrieveUpdateAPIView):
    serializer_class = SchoolClassCatalogSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )

    def get_queryset(self):
        queryset = SchoolClass.objects.select_related("year")
        if self.request.method not in SAFE_METHODS:
            queryset = queryset.select_for_update()
        return queryset

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            return super().update(request, *args, **kwargs)


@extend_schema_view(
    get=extend_schema(
        summary="Sinf tanlash ro‘yxati",
        description="Course formasi uchun ID, yil ID, sinf darajasi va section’ni beradi.",
        tags=["Academics"],
        responses={
            200: SchoolClassOptionSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class SchoolClassOptionListView(ListAPIView):
    queryset = SchoolClass.objects.select_related("year")
    serializer_class = SchoolClassOptionSerializer


@extend_schema_view(
    get=extend_schema(
        summary="Faol fanlar katalogi",
        description="Arxivlanmagan fanlarni read-only qaytaradi.",
        tags=["Academics"],
        responses={
            200: SubjectCatalogSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class SubjectCatalogListView(ListAPIView):
    serializer_class = SubjectCatalogSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )

    def get_queryset(self):
        return Subject.objects.filter(archived_at__isnull=True)


@extend_schema_view(
    get=extend_schema(
        summary="Fanlarni boshqarish ro‘yxati",
        description=(
            "Teacher va pupil read-only ko‘radi; arxivlangan fanlar ham "
            "tarix uchun ro‘yxatda qoladi."
        ),
        tags=["Academics"],
        responses={
            200: SubjectAdminSerializer(many=True),
            **common_error_responses(),
        },
    ),
    post=extend_schema(
        summary="Fan yaratish",
        description="manage_academics huquqli operator unique code bilan fan yaratadi.",
        tags=["Academics"],
        request=SubjectAdminSerializer,
        responses={
            201: SubjectAdminSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Fan yaratish",
                value={"code": "MATH", "title": "Matematika"},
                request_only=True,
            ),
        ],
    ),
)
class SubjectAdminListCreateView(ListCreateAPIView):
    queryset = Subject.objects.all()
    serializer_class = SubjectAdminSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )


@extend_schema_view(
    get=extend_schema(
        summary="Fan tafsiloti",
        description="Arxivlangan yoki faol bitta fan katalog ma’lumotini ko‘rsatadi.",
        tags=["Academics"],
        responses={
            200: SubjectAdminSerializer,
            **common_error_responses(),
        },
    ),
    put=extend_schema(
        summary="Fan ma’lumotini to‘liq yangilash",
        description="manage_academics huquqi kerak; arxivlangan fan tahrirlanmaydi.",
        tags=["Academics"],
        request=SubjectAdminSerializer,
        responses={
            200: SubjectAdminSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Fan ma’lumotlari",
                value={"code": "MATH", "title": "Matematika"},
                request_only=True,
            ),
        ],
    ),
    patch=extend_schema(
        summary="Fan ma’lumotini qisman yangilash",
        description="manage_academics huquqi kerak; arxivlangan fan tahrirlanmaydi.",
        tags=["Academics"],
        request=SubjectAdminSerializer,
        responses={
            200: SubjectAdminSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                "Fan nomini yangilash",
                value={"title": "Matematika"},
                request_only=True,
            ),
        ],
    ),
)
class SubjectAdminRetrieveUpdateView(RetrieveUpdateAPIView):
    serializer_class = SubjectAdminSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )

    def get_queryset(self):
        queryset = Subject.objects.all()
        if self.request.method not in SAFE_METHODS:
            queryset = queryset.select_for_update()
        return queryset

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            return super().update(request, *args, **kwargs)


@extend_schema_view(
    post=extend_schema(
        summary="Fanni arxivlash",
        description=(
            "manage_academics huquqli operator fanni o‘chirmasdan "
            "selectorlardan yashiradi; mavjud tarix saqlanadi."
        ),
        tags=["Academics"],
        request=None,
        responses={
            200: SubjectAdminSerializer,
            **common_error_responses(),
        },
    ),
)
class SubjectArchiveView(GenericAPIView):
    queryset = Subject.objects.all()
    serializer_class = SubjectAdminSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsCatalogReadOrManager,
    )

    def get_queryset(self):
        return Subject.objects.select_for_update()

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        subject = self.get_object()
        if subject.archived_at is None:
            subject.archived_at = timezone.now()
            subject.save(update_fields=("archived_at",))
        return Response(self.get_serializer(subject).data)


@extend_schema_view(
    get=extend_schema(
        summary="Sinf uchun mavjud fanlar",
        description=(
            "Berilgan sinfda course bo‘lmagan va arxivlanmagan fanlarni "
            "CourseOffering formasi uchun qaytaradi."
        ),
        tags=["Academics"],
        parameters=[
            OpenApiParameter(
                name="school_class_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Sinf IDsi.",
            ),
        ],
        responses={
            200: SubjectCatalogSerializer(many=True),
            **common_error_responses(),
        },
    ),
)
class AvailableSubjectOptionListView(ListAPIView):
    serializer_class = SubjectCatalogSerializer

    def get_queryset(self):
        school_class = get_object_or_404(
            SchoolClass,
            pk=self.kwargs["school_class_id"],
        )
        used_subjects = CourseOffering.objects.filter(
            school_class=school_class,
        ).values("subject_id")
        return Subject.objects.filter(
            archived_at__isnull=True,
        ).exclude(pk__in=used_subjects)
