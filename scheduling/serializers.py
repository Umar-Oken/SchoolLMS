from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from academics.models import CourseOffering
from accounts.models import User
from scheduling.models import (
    Attendance,
    AttendanceAudit,
    LessonSession,
    Room,
    ScheduleException,
    WeeklySlot,
)
from scheduling.services import (
    apply_schedule_exception,
    save_session_attendance,
    save_weekly_slot,
)


class AttendanceInputListSerializer(serializers.ListSerializer):
    def validate(self, attrs):
        pupil_ids = [row['pupil'].pk for row in attrs]
        if len(pupil_ids) != len(set(pupil_ids)):
            raise serializers.ValidationError(
                'Bir pupil faqat bir marta yuborilishi mumkin.',
            )
        return attrs

    def create(self, validated_data):
        return save_session_attendance(
            session_id=self.context['session'].pk,
            actor=self.context['request'].user,
            rows=validated_data,
        )


class AttendanceInputSerializer(serializers.Serializer):
    pupil = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.PUPIL, is_active=True),
    )
    status = serializers.ChoiceField(choices=Attendance.Status.choices)
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )

    class Meta:
        list_serializer_class = AttendanceInputListSerializer

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - {'pupil', 'status', 'reason'}
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)


class AttendanceAuditReadSerializer(serializers.ModelSerializer):
    actor = serializers.IntegerField(source='actor_id', read_only=True)

    class Meta:
        model = AttendanceAudit
        fields = ('old_status', 'new_status', 'reason', 'actor', 'created_at')
        read_only_fields = fields


class AttendanceReadSerializer(serializers.Serializer):
    pupil = serializers.SerializerMethodField()
    status = serializers.ChoiceField(choices=Attendance.Status.choices)
    history = serializers.SerializerMethodField()

    @extend_schema_field(serializers.IntegerField)
    def get_pupil(self, attendance):
        if isinstance(attendance, dict):
            return attendance.get('pupil', attendance.get('pupil_id'))
        return attendance.pupil_id

    @extend_schema_field(AttendanceAuditReadSerializer(many=True))
    def get_history(self, attendance):
        if isinstance(attendance, dict):
            return AttendanceAuditReadSerializer(
                attendance.get('history', attendance.get('audit_entries', [])),
                many=True,
            ).data
        return AttendanceAuditReadSerializer(
            attendance.audit_entries.all(),
            many=True,
        ).data


class AttendanceWriteReadSerializer(serializers.ModelSerializer):
    pupil = serializers.IntegerField(source='pupil_id', read_only=True)

    class Meta:
        model = Attendance
        fields = ('pupil', 'status')
        read_only_fields = fields


class AttendanceHistoryEntrySerializer(serializers.Serializer):
    old_status = serializers.ChoiceField(choices=Attendance.Status.choices)
    new_status = serializers.ChoiceField(choices=Attendance.Status.choices)
    reason = serializers.CharField()
    actor = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class PupilAttendanceEntrySerializer(serializers.Serializer):
    session = serializers.IntegerField()
    course = serializers.IntegerField()
    scheduled_date = serializers.DateField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=Attendance.Status.choices)
    history = AttendanceHistoryEntrySerializer(many=True)


class AttendanceSummarySerializer(serializers.Serializer):
    eligible_lessons = serializers.IntegerField()
    marked_lessons = serializers.IntegerField()
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()
    unmarked = serializers.IntegerField()


class PupilAttendanceReadSerializer(serializers.Serializer):
    summary = AttendanceSummarySerializer()
    results = PupilAttendanceEntrySerializer(many=True)


class SessionAttendanceSummarySerializer(serializers.Serializer):
    eligible_pupils = serializers.IntegerField()
    marked_pupils = serializers.IntegerField()
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()
    unmarked = serializers.IntegerField()


class SessionAttendanceReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    course = serializers.IntegerField()
    scheduled_date = serializers.DateField()
    status = serializers.CharField()
    summary = SessionAttendanceSummarySerializer()
    attendance = AttendanceReadSerializer(many=True)


class AttendanceResultSerializer(serializers.Serializer):
    session = serializers.IntegerField()
    attendance = AttendanceWriteReadSerializer(many=True)


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ('id', 'name')
        read_only_fields = ('id',)

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - {'name'}
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)


class WeeklySlotSerializer(serializers.ModelSerializer):
    course = serializers.PrimaryKeyRelatedField(
        queryset=CourseOffering.objects.all(),
    )
    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    room = serializers.PrimaryKeyRelatedField(queryset=Room.objects.all())

    class Meta:
        model = WeeklySlot
        fields = (
            'id',
            'course',
            'teacher',
            'room',
            'weekday',
            'start_time',
            'end_time',
            'valid_from',
            'valid_to',
        )
        read_only_fields = ('id',)

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            allowed_fields = {
                'course',
                'teacher',
                'room',
                'weekday',
                'start_time',
                'end_time',
                'valid_from',
                'valid_to',
            }
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        current = self.instance
        slot = WeeklySlot(
            course=attrs.get('course', current.course if current else None),
            teacher=attrs.get('teacher', current.teacher if current else None),
            room=attrs.get('room', current.room if current else None),
            weekday=attrs.get('weekday', current.weekday if current else None),
            start_time=attrs.get(
                'start_time',
                current.start_time if current else None,
            ),
            end_time=attrs.get(
                'end_time',
                current.end_time if current else None,
            ),
            valid_from=attrs.get(
                'valid_from',
                current.valid_from if current else None,
            ),
            valid_to=attrs.get(
                'valid_to',
                current.valid_to if current else None,
            ),
        )
        try:
            slot.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages,
            ) from exc
        return attrs

    def create(self, validated_data):
        return self._save(validated_data)

    def update(self, instance, validated_data):
        return self._save(validated_data, instance=instance)

    @staticmethod
    def _save(validated_data, instance=None):
        try:
            return save_weekly_slot(
                validated_data=validated_data,
                instance=instance,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages,
            ) from exc


class ScheduleWindowSerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()

    def to_internal_value(self, data):
        allowed_fields = {'from', 'to'}
        extra_fields = set(data.keys()) - allowed_fields
        if extra_fields:
            raise serializers.ValidationError({
                name: 'Bu query parametri qabul qilinmaydi.'
                for name in extra_fields
            })
        return super().to_internal_value({
            'from_date': data.get('from'),
            'to_date': data.get('to'),
        })

    def validate(self, attrs):
        from_date = attrs['from_date']
        to_date = attrs['to_date']
        if from_date > to_date:
            raise serializers.ValidationError({
                'to': 'Tugash sanasi boshlanish sanasidan oldin bo‘lishi mumkin emas.',
            })
        if (to_date - from_date).days + 1 > 31:
            raise serializers.ValidationError({
                'to': 'So‘rov oynasi ko‘pi bilan 31 kalendar kun bo‘lishi mumkin.',
            })
        return attrs


class LessonSessionReadSerializer(serializers.ModelSerializer):
    course = serializers.IntegerField(source='slot.course_id', read_only=True)
    status = serializers.CharField(read_only=True)
    teacher = serializers.IntegerField(
        source='effective_teacher_id',
        read_only=True,
    )
    room = serializers.IntegerField(
        source='effective_room_id',
        read_only=True,
    )

    class Meta:
        model = LessonSession
        fields = (
            'id',
            'slot',
            'course',
            'teacher',
            'room',
            'local_date',
            'scheduled_date',
            'starts_at',
            'ends_at',
            'status',
        )
        read_only_fields = fields


class ScheduleExceptionCommandSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=ScheduleException.Action.choices)
    reason = serializers.CharField(max_length=2000)
    scheduled_date = serializers.DateField(required=False)
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    teacher = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(
            role=User.Role.TEACHER,
            is_active=True,
        ),
        required=False,
    )
    room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(),
        required=False,
    )

    def to_internal_value(self, data):
        allowed_fields = {
            'action',
            'reason',
            'scheduled_date',
            'start_time',
            'end_time',
            'teacher',
            'room',
        }
        extra_fields = set(data.keys()) - allowed_fields
        if extra_fields:
            raise serializers.ValidationError({
                name: 'Bu maydon qabul qilinmaydi.'
                for name in extra_fields
            })
        return super().to_internal_value(data)

    def validate(self, attrs):
        if not attrs['reason'].strip():
            raise serializers.ValidationError({
                'reason': 'Sabab bo‘sh bo‘lishi mumkin emas.',
            })
        if attrs['action'] == ScheduleException.Action.MOVED:
            required_fields = {'scheduled_date', 'start_time', 'end_time'}
            missing = required_fields - attrs.keys()
            if missing:
                raise serializers.ValidationError({
                    name: 'Ko‘chirish uchun bu maydon majburiy.'
                    for name in missing
                })
            if attrs['start_time'] >= attrs['end_time']:
                raise serializers.ValidationError({
                    'end_time': 'Tugash vaqti boshlanish vaqtidan keyin bo‘lishi kerak.',
                })
        elif set(attrs) - {'action', 'reason'}:
            raise serializers.ValidationError(
                'Bekor qilish uchun faqat action va reason yuboring.'
            )
        return attrs

    def create(self, validated_data):
        try:
            return apply_schedule_exception(
                session_id=self.context['session'].pk,
                actor=self.context['request'].user,
                **validated_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, 'message_dict') else exc.messages,
            ) from exc
