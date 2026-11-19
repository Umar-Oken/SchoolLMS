from collections import Counter

from django.db.models import Exists, OuterRef, Prefetch, Q
from rest_framework.generics import (
    GenericAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateAPIView,
)
from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    IsAuthenticated,
)
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema

from academics.models import Enrollment, TeachingAssignment
from accounts.models import User
from accounts.permissions import CanManageAcademics, MustChangePasswordPermission
from scheduling.models import (
    Attendance,
    LessonSession,
    Room,
    WeeklySlot,
)
from scheduling.serializers import (
    AttendanceInputSerializer,
    AttendanceReadSerializer,
    AttendanceResultSerializer,
    LessonSessionReadSerializer,
    PupilAttendanceEntrySerializer,
    PupilAttendanceReadSerializer,
    RoomSerializer,
    ScheduleExceptionCommandSerializer,
    ScheduleWindowSerializer,
    SessionAttendanceReadSerializer,
    WeeklySlotSerializer,
)
from root.openapi import common_error_responses


class IsAssignedSessionTeacher(BasePermission):
    message = 'Faqat shu darsga tayinlangan teacher davomat belgilashi mumkin.'

    def has_object_permission(self, request, view, session):
        user = request.user
        return (
            user.role == User.Role.TEACHER
            and user.pk == session.effective_teacher_id
            and TeachingAssignment.objects.filter(
                course_id=session.slot.course_id,
                teacher_id=user.pk,
                start_date__lte=session.scheduled_date,
            ).filter(
                Q(end_date__isnull=True)
                | Q(end_date__gte=session.scheduled_date),
            ).exists()
        )


class IsPupil(BasePermission):
    message = 'Faqat pupil o‘z davomatini ko‘ra oladi.'

    def has_permission(self, request, view):
        return request.user.role == User.Role.PUPIL


def _attendance_summary(statuses):
    counts = Counter(statuses)
    return {
        'eligible_lessons': len(statuses),
        'marked_lessons': sum(
            counts[status]
            for status in (
                Attendance.Status.PRESENT,
                Attendance.Status.ABSENT,
                Attendance.Status.LATE,
                Attendance.Status.EXCUSED,
            )
        ),
        **{
            status: counts[status]
            for status in Attendance.Status.values
        },
    }


class CanReadOrManageScheduling(CanManageAcademics):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return super().has_permission(request, view)


class RoomListCreateView(ListCreateAPIView):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanReadOrManageScheduling,
    )


class RoomDetailView(RetrieveUpdateAPIView):
    queryset = Room.objects.all()
    serializer_class = RoomSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanReadOrManageScheduling,
    )


class WeeklySlotListCreateView(ListCreateAPIView):
    queryset = WeeklySlot.objects.select_related('course', 'teacher', 'room')
    serializer_class = WeeklySlotSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanReadOrManageScheduling,
    )


class WeeklySlotDetailView(RetrieveUpdateAPIView):
    queryset = WeeklySlot.objects.select_related('course', 'teacher', 'room')
    serializer_class = WeeklySlotSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanReadOrManageScheduling,
    )


class ScheduleView(ListAPIView):
    serializer_class = LessonSessionReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def get_queryset(self):
        window = ScheduleWindowSerializer(data=self.request.query_params)
        window.is_valid(raise_exception=True)
        from_date = window.validated_data['from_date']
        to_date = window.validated_data['to_date']
        queryset = (
            LessonSession.objects.filter(
                scheduled_date__range=(from_date, to_date),
            )
            .select_related('slot', 'slot__course', 'slot__teacher', 'slot__room')
        )

        if self.request.user.role == User.Role.TEACHER:
            queryset = queryset.filter(
                Q(slot__teacher_id=self.request.user.pk)
                | Q(teacher_override_id=self.request.user.pk),
            )
            assignment = TeachingAssignment.objects.filter(
                course_id=OuterRef('slot__course_id'),
                teacher_id=self.request.user.pk,
                start_date__lte=OuterRef('scheduled_date'),
            ).filter(
                Q(end_date__isnull=True)
                | Q(end_date__gte=OuterRef('scheduled_date')),
            )
            return queryset.annotate(
                is_assigned_teacher=Exists(assignment),
            ).filter(is_assigned_teacher=True)

        if self.request.user.role == User.Role.PUPIL:
            enrollment = Enrollment.objects.filter(
                course_id=OuterRef('slot__course_id'),
                pupil_id=self.request.user.pk,
                start_date__lte=OuterRef('scheduled_date'),
            ).filter(
                Q(end_date__isnull=True)
                | Q(end_date__gte=OuterRef('scheduled_date')),
            )
            return queryset.annotate(
                is_enrolled_pupil=Exists(enrollment),
            ).filter(is_enrolled_pupil=True)

        return queryset.none()


class MyAttendanceView(ListAPIView):
    serializer_class = PupilAttendanceEntrySerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsPupil,
    )

    def get_queryset(self):
        window = ScheduleWindowSerializer(data=self.request.query_params)
        window.is_valid(raise_exception=True)
        from_date = window.validated_data['from_date']
        to_date = window.validated_data['to_date']
        enrollment = Enrollment.objects.filter(
            course_id=OuterRef('slot__course_id'),
            pupil_id=self.request.user.pk,
            start_date__lte=OuterRef('scheduled_date'),
        ).filter(
            Q(end_date__isnull=True)
            | Q(end_date__gte=OuterRef('scheduled_date')),
        )
        pupil_attendance = Attendance.objects.filter(
            pupil_id=self.request.user.pk,
        ).prefetch_related('audit_entries')
        return (
            LessonSession.objects.filter(
                scheduled_date__range=(from_date, to_date),
            )
            .exclude(status=LessonSession.Status.CANCELLED)
            .annotate(is_enrolled=Exists(enrollment))
            .filter(is_enrolled=True)
            .select_related('slot')
            .prefetch_related(
                Prefetch(
                    'attendance_records',
                    queryset=pupil_attendance,
                    to_attr='pupil_attendance',
                ),
            )
        )

    @extend_schema(
        summary='Pupilning shaxsiy davomat tarixi',
        description=(
            'Faqat so‘rov yuborgan pupilning dars sanasidagi enrollment '
            'sessiyalari qaytadi. Bekor qilingan darslar maxrajga kirmaydi; '
            '`unmarked` kelgan deb hisoblanmaydi.'
        ),
        tags=['Scheduling'],
        parameters=[
            OpenApiParameter(
                name='from',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=True,
                description='Mahalliy dars oynasining boshlanish sanasi.',
            ),
            OpenApiParameter(
                name='to',
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=True,
                description='Mahalliy dars oynasining tugash sanasi; oyna 31 kungacha.',
            ),
        ],
        responses={
            200: PupilAttendanceReadSerializer,
            **common_error_responses(),
        },
    )
    def list(self, request, *args, **kwargs):
        sessions = list(self.get_queryset())
        statuses = []
        results = []
        for session in sessions:
            attendance = (
                session.pupil_attendance[0]
                if session.pupil_attendance
                else None
            )
            current_status = (
                attendance.status
                if attendance
                else Attendance.Status.UNMARKED
            )
            statuses.append(current_status)
            results.append({
                'session': session.pk,
                'course': session.slot.course_id,
                'scheduled_date': session.scheduled_date,
                'starts_at': session.starts_at,
                'ends_at': session.ends_at,
                'status': current_status,
                'history': (
                    attendance.audit_entries.all()
                    if attendance
                    else []
                ),
            })

        payload = {
            'summary': _attendance_summary(statuses),
            'results': PupilAttendanceEntrySerializer(results, many=True).data,
        }
        return Response(PupilAttendanceReadSerializer(payload).data)


class ScheduleExceptionView(GenericAPIView):
    serializer_class = ScheduleExceptionCommandSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        CanManageAcademics,
    )

    def get_queryset(self):
        return LessonSession.objects.select_related(
            'slot',
            'slot__course',
            'slot__teacher',
            'slot__room',
            'teacher_override',
            'room_override',
        )

    def post(self, request, *args, **kwargs):
        session = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                'session': session,
            },
        )
        serializer.is_valid(raise_exception=True)
        updated_session = serializer.save()
        return Response(LessonSessionReadSerializer(updated_session).data)


class SessionAttendanceView(GenericAPIView):
    serializer_class = AttendanceInputSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsAssignedSessionTeacher,
    )

    def get_queryset(self):
        return LessonSession.objects.select_related(
            'slot',
            'slot__course',
            'slot__teacher',
            'teacher_override',
        )

    @extend_schema(
        summary='Bitta dars davomatini atomar belgilash',
        description=(
            'Faqat session sanasida kursga biriktirilgan amaldagi teacher '
            'foydalanadi. Takroriy pupil yoki yaroqsiz qator butun batch’ni '
            'bekor qiladi; mavjud holat o‘zgarsa reason majburiy.'
        ),
        tags=['Scheduling'],
        request=AttendanceInputSerializer(many=True),
        responses={
            200: AttendanceResultSerializer,
            **common_error_responses(),
        },
    )
    def put(self, request, *args, **kwargs):
        session = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            many=True,
            context={
                **self.get_serializer_context(),
                'session': session,
            },
        )
        serializer.is_valid(raise_exception=True)
        records = serializer.save()
        return Response(AttendanceResultSerializer({
            'session': session.pk,
            'attendance': records,
        }).data)

    @extend_schema(
        summary='Dars davomatini va tuzatish tarixini ko‘rish',
        description=(
            'Faqat session sanasida kursga biriktirilgan amaldagi teacher '
            'ko‘radi. Enrollment’dagi, lekin hali belgilanmagan pupil '
            '`unmarked` bo‘lib qaytadi; bekor qilingan session statistika '
            'maxrajiga kirmaydi.'
        ),
        tags=['Scheduling'],
        responses={
            200: SessionAttendanceReadSerializer,
            **common_error_responses(),
        },
    )
    def get(self, request, *args, **kwargs):
        session = self.get_object()
        enrollments = Enrollment.objects.filter(
            course_id=session.slot.course_id,
            start_date__lte=session.scheduled_date,
            pupil__role=User.Role.PUPIL,
        ).filter(
            Q(end_date__isnull=True)
            | Q(end_date__gte=session.scheduled_date),
        ).select_related('pupil').order_by('pupil_id')
        attendance_records = {
            record.pupil_id: record
            for record in Attendance.objects.filter(
                session=session,
            ).prefetch_related('audit_entries')
        }

        if session.status == LessonSession.Status.CANCELLED:
            attendance = list(attendance_records.values())
            statuses = []
            eligible_pupils = 0
        else:
            attendance = []
            statuses = []
            for enrollment in enrollments:
                record = attendance_records.get(enrollment.pupil_id)
                pupil_status = (
                    record.status
                    if record
                    else Attendance.Status.UNMARKED
                )
                statuses.append(pupil_status)
                attendance.append(
                    record
                    if record
                    else {
                        'pupil_id': enrollment.pupil_id,
                        'status': pupil_status,
                        'audit_entries': [],
                    }
                )
            eligible_pupils = len(statuses)

        summary = _attendance_summary(statuses)
        payload = {
            'id': session.pk,
            'course': session.slot.course_id,
            'scheduled_date': session.scheduled_date,
            'status': session.status,
            'summary': {
                'eligible_pupils': eligible_pupils,
                'marked_pupils': summary['marked_lessons'],
                **{
                    status: summary[status]
                    for status in Attendance.Status.values
                },
            },
            'attendance': AttendanceReadSerializer(attendance, many=True).data,
        }
        return Response(SessionAttendanceReadSerializer(payload).data)
