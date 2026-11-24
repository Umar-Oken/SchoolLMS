from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from drf_spectacular.utils import extend_schema_field

from academics.models import CourseOffering
from academics.views import teacher_course_scope
from assessments.models import Answer, Attempt, AttemptGrade, AttemptGradeAudit, AttemptItem, AttemptOption, AttemptResult, Exam, ExamItem, Option, Question
from assessments.models import ExamSnapshotItem, ExamSnapshotOption


class StrictInputSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            writable_fields = {
                name for name, field in self.fields.items()
                if not field.read_only
            }
            extra_fields = set(data.keys()) - writable_fields
            if extra_fields:
                raise serializers.ValidationError({
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in extra_fields
                })
        return super().to_internal_value(data)


class TeacherQuestionOptionSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - {'text', 'is_correct'}
            if extra_fields:
                raise serializers.ValidationError({
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in extra_fields
                })
        return super().to_internal_value(data)

    class Meta:
        model = Option
        fields = ('id', 'text', 'is_correct')
        read_only_fields = ('id',)


class TeacherQuestionSerializer(StrictInputSerializer):
    course = serializers.PrimaryKeyRelatedField(
        queryset=CourseOffering.objects.all(),
    )
    stem = serializers.CharField(allow_blank=False)
    type = serializers.ChoiceField(
        choices=Question.Type.choices,
        default=Question.Type.SINGLE_CHOICE,
    )
    version = serializers.IntegerField(read_only=True)
    state = serializers.ChoiceField(
        choices=Question.State.choices,
        read_only=True,
    )
    marking_note = serializers.CharField(
        allow_blank=True,
        required=False,
        default='',
    )
    options = TeacherQuestionOptionSerializer(
        many=True,
        min_length=1,
        required=False,
    )

    class Meta:
        model = Question
        fields = (
            'id', 'course', 'stem', 'type', 'version', 'state', 'marking_note',
            'options',
        )
        read_only_fields = ('id', 'type', 'version', 'state')

    def validate_course(self, course):
        request = self.context['request']
        if not teacher_course_scope(request.user, active_only=True).filter(
            pk=course.pk,
        ).exists():
            raise NotFound()
        return course

    def validate(self, attrs):
        question_type = attrs.get('type', Question.Type.SINGLE_CHOICE)
        options_supplied = 'options' in self.initial_data
        options = attrs.get('options')
        if question_type == Question.Type.ESSAY:
            if options_supplied:
                raise serializers.ValidationError({
                    'options': 'Essay savolga option kaliti yuborilmaydi.'
                })
            return attrs

        if options is None:
            raise serializers.ValidationError({
                'options': 'Single-choice savol uchun optionlar majburiy.'
            })
        correct_count = sum(option['is_correct'] for option in options)
        if correct_count != 1:
            raise serializers.ValidationError({
                'options': (
                    'Single-choice savolda aynan bitta correct option bo‘lishi kerak.'
                )
            })
        return attrs

    def create(self, validated_data):
        options = validated_data.pop('options', [])
        with transaction.atomic():
            question = Question.objects.create(**validated_data)
            if options:
                Option.objects.bulk_create([
                    Option(question=question, **option) for option in options
                ])
        return Question.objects.prefetch_related('options').get(pk=question.pk)


class TeacherQuestionUpdateSerializer(TeacherQuestionSerializer):
    course = serializers.PrimaryKeyRelatedField(read_only=True)
    type = serializers.ChoiceField(
        choices=Question.Type.choices,
        read_only=True,
    )
    stem = serializers.CharField(allow_blank=False, required=False)
    marking_note = serializers.CharField(allow_blank=True, required=False)

    class Meta(TeacherQuestionSerializer.Meta):
        read_only_fields = (
            'id', 'course', 'type', 'version', 'state',
        )

    def validate(self, attrs):
        question = self.instance
        options_supplied = 'options' in self.initial_data
        options = attrs.get('options')
        if question.type == Question.Type.ESSAY:
            if options_supplied:
                raise serializers.ValidationError({
                    'options': 'Essay savolga option kaliti yuborilmaydi.'
                })
            return attrs

        if options is not None:
            correct_count = sum(option['is_correct'] for option in options)
            if correct_count != 1:
                raise serializers.ValidationError({
                    'options': (
                        'Single-choice savolda aynan bitta correct option bo‘lishi kerak.'
                    )
                })
        return attrs

    def update(self, instance, validated_data):
        options = validated_data.pop('options', None)
        with transaction.atomic():
            if instance.state == Question.State.PUBLISHED:
                next_question = Question.objects.create(
                    course=instance.course,
                    stem=validated_data.get('stem', instance.stem),
                    family_id=instance.family_id,
                    type=instance.type,
                    version=instance.version + 1,
                    marking_note=validated_data.get(
                        'marking_note', instance.marking_note,
                    ),
                    state=Question.State.DRAFT,
                )
                source_options = options
                if source_options is None and instance.type == Question.Type.SINGLE_CHOICE:
                    source_options = list(instance.options.values('text', 'is_correct'))
                if source_options is not None:
                    Option.objects.bulk_create([
                        Option(question=next_question, **option)
                        for option in source_options
                    ])
                return Question.objects.prefetch_related('options').get(
                    pk=next_question.pk,
                )

            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            if options is not None:
                instance.options.all().delete()
                Option.objects.bulk_create([
                    Option(question=instance, **option) for option in options
                ])
        return Question.objects.prefetch_related('options').get(pk=instance.pk)


class QuestionArchiveSerializer(serializers.Serializer):
    def update(self, instance, validated_data):
        instance.state = Question.State.ARCHIVED
        instance.archived_at = timezone.now()
        instance.save(update_fields=('state', 'archived_at'))
        return instance


class ExamMetadataSerializer(StrictInputSerializer):
    course = serializers.PrimaryKeyRelatedField(
        queryset=CourseOffering.objects.all(),
    )
    duration = serializers.DurationField(min_value=timedelta(seconds=1))
    attempt_limit = serializers.IntegerField(min_value=1, max_value=32767)
    result_release_at = serializers.DateTimeField(required=True)
    state = serializers.ChoiceField(choices=Exam.State.choices, read_only=True)
    published_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Exam
        fields = (
            'id', 'course', 'term', 'title', 'opens_at', 'closes_at', 'duration',
            'attempt_limit', 'result_release_at', 'state',
            'published_at',
        )
        read_only_fields = ('id', 'state', 'published_at')

    def validate_course(self, course):
        request = self.context['request']
        if not teacher_course_scope(request.user, active_only=True).filter(
            pk=course.pk,
        ).exists():
            raise NotFound()
        return course

    def validate(self, attrs):
        course = attrs.get('course', getattr(self.instance, 'course', None))
        term = attrs.get('term', getattr(self.instance, 'term', None))
        opens_at = attrs.get('opens_at', getattr(self.instance, 'opens_at', None))
        closes_at = attrs.get('closes_at', getattr(self.instance, 'closes_at', None))
        duration = attrs.get('duration', getattr(self.instance, 'duration', None))
        result_release_at = attrs.get(
            'result_release_at',
            getattr(self.instance, 'result_release_at', None),
        )
        errors = {}
        if course and term and course.school_class.year_id != term.year_id:
            errors['term'] = 'Term va course bir o‘quv yiliga tegishli bo‘lishi kerak.'
        if opens_at and closes_at and opens_at >= closes_at:
            errors['closes_at'] = 'closes_at opens_at dan keyin bo‘lishi kerak.'
        if duration and duration.total_seconds() <= 0:
            errors['duration'] = 'duration musbat bo‘lishi kerak.'
        if result_release_at and closes_at and result_release_at < closes_at:
            errors['result_release_at'] = (
                'result_release_at closes_at dan oldin bo‘lishi mumkin emas.'
            )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ExamMetadataUpdateSerializer(ExamMetadataSerializer):
    course = serializers.PrimaryKeyRelatedField(read_only=True)
    term = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta(ExamMetadataSerializer.Meta):
        read_only_fields = ('id', 'course', 'term', 'state', 'published_at')


class PupilExamCardSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = Exam
        fields = (
            'id', 'title', 'opens_at', 'closes_at', 'duration', 'attempt_limit',
            'status',
        )
        read_only_fields = fields

    def get_status(self, exam) -> str:
        now = timezone.now()
        if now < exam.opens_at:
            return 'upcoming'
        if now >= exam.closes_at:
            return 'closed'
        return 'open'


class TeacherExamPreviewOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamSnapshotOption
        fields = ('text', 'is_correct', 'position')
        read_only_fields = fields


class TeacherExamPreviewItemSerializer(serializers.ModelSerializer):
    options = TeacherExamPreviewOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ExamSnapshotItem
        fields = (
            'source_question_id', 'source_question_version', 'stem', 'type',
            'marking_note', 'points', 'position', 'options',
        )
        read_only_fields = fields


class TeacherExamPreviewSerializer(serializers.ModelSerializer):
    snapshot_items = TeacherExamPreviewItemSerializer(many=True, read_only=True)

    class Meta:
        model = Exam
        fields = (
            'id', 'course', 'term', 'title', 'opens_at', 'closes_at', 'duration',
            'attempt_limit', 'result_release_at', 'state', 'published_at',
            'snapshot_items',
        )
        read_only_fields = fields


class PupilAttemptOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttemptOption
        fields = ('id', 'text', 'position')
        read_only_fields = fields


class PupilAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Answer
        fields = ('selected_option', 'essay_text', 'version', 'saved_at')
        read_only_fields = fields


class PupilAttemptItemSerializer(serializers.ModelSerializer):
    options = PupilAttemptOptionSerializer(many=True, read_only=True)
    answer = serializers.SerializerMethodField()

    @extend_schema_field(PupilAnswerSerializer(allow_null=True))
    def get_answer(self, item):
        answer = getattr(item, 'answer', None)
        if answer is None:
            return None
        return PupilAnswerSerializer(answer).data

    class Meta:
        model = AttemptItem
        fields = ('id', 'stem', 'type', 'position', 'options', 'answer')
        read_only_fields = fields


class PupilAttemptSerializer(serializers.ModelSerializer):
    items = PupilAttemptItemSerializer(many=True, read_only=True)
    server_now = serializers.SerializerMethodField()

    def get_server_now(self, attempt) -> datetime:
        return timezone.now()

    class Meta:
        model = Attempt
        fields = ('id', 'attempt_number', 'started_at', 'expires_at', 'state', 'server_now', 'items')
        read_only_fields = fields


class AttemptFinishSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttemptResult
        fields = ('status', 'finished_at')
        read_only_fields = fields


class TeacherAttemptGradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttemptGrade
        fields = ('points', 'feedback', 'version', 'graded_at')
        read_only_fields = fields


class AttemptGradeAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttemptGradeAudit
        fields = (
            'old_points', 'new_points', 'old_feedback', 'new_feedback',
            'version', 'changed_at',
        )
        read_only_fields = fields


class TeacherEssayGradeInputSerializer(serializers.Serializer):
    points = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal('0'))
    feedback = serializers.CharField(required=False, allow_blank=True, default='')
    expected_version = serializers.IntegerField(min_value=0)

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - set(self.fields)
            if extra_fields:
                raise serializers.ValidationError({
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in extra_fields
                })
        return super().to_internal_value(data)

    def validate_points(self, points):
        if points > self.context['max_score']:
            raise serializers.ValidationError('Ball item max_score qiymatidan katta.')
        return points


class TeacherEssayItemSerializer(serializers.ModelSerializer):
    answer_text = serializers.SerializerMethodField()
    grade = TeacherAttemptGradeSerializer(source='manual_grade', allow_null=True, read_only=True)
    audit = serializers.SerializerMethodField()
    max_score = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DecimalField(max_digits=8, decimal_places=2))
    def get_max_score(self, item):
        return self.context.get('max_scores', {}).get(item.source_snapshot_item_id)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_answer_text(self, item):
        answer = getattr(item, 'answer', None)
        return None if answer is None else answer.essay_text

    @extend_schema_field(AttemptGradeAuditSerializer(many=True))
    def get_audit(self, item):
        grade = getattr(item, 'manual_grade', None)
        if grade is None:
            return []
        return AttemptGradeAuditSerializer(grade.audits.all(), many=True).data

    class Meta:
        model = AttemptItem
        fields = ('id', 'stem', 'max_score', 'answer_text', 'grade', 'audit')
        read_only_fields = fields


class TeacherAttemptGradingSerializer(serializers.ModelSerializer):
    essay_items = TeacherEssayItemSerializer(many=True, read_only=True)

    class Meta:
        model = Attempt
        fields = ('id', 'pupil', 'state', 'essay_items')
        read_only_fields = fields


class PupilAttemptResultSerializer(serializers.ModelSerializer):
    total_score = serializers.SerializerMethodField()
    total_max_score = serializers.SerializerMethodField()

    @extend_schema_field(
        serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True),
    )
    def get_total_score(self, result):
        return result.total_score if result.status == AttemptResult.Status.FINALIZED else None

    @extend_schema_field(
        serializers.DecimalField(max_digits=8, decimal_places=2, allow_null=True),
    )
    def get_total_max_score(self, result):
        return result.total_max_score if result.status == AttemptResult.Status.FINALIZED else None

    class Meta:
        model = AttemptResult
        fields = ('status', 'total_score', 'total_max_score', 'finished_at')
        read_only_fields = fields


class PupilExamGradebookSerializer(serializers.ModelSerializer):
    source_kind = serializers.SerializerMethodField()
    attempt_id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(source='attempt.exam.title', read_only=True)
    course_id = serializers.IntegerField(source='attempt.exam.course_id', read_only=True)
    term_id = serializers.IntegerField(source='attempt.exam.term_id', read_only=True)

    @extend_schema_field(serializers.ChoiceField(choices=('exam',)))
    def get_source_kind(self, result):
        return 'exam'

    class Meta:
        model = AttemptResult
        fields = (
            'source_kind', 'attempt_id', 'title', 'course_id', 'term_id',
            'total_score', 'total_max_score', 'finished_at',
        )
        read_only_fields = fields


class TeacherExamGradebookSerializer(PupilExamGradebookSerializer):
    pupil_id = serializers.IntegerField(source='attempt.pupil_id', read_only=True)
    pupil_name = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)

    @extend_schema_field(serializers.CharField())
    def get_pupil_name(self, result):
        return result.attempt.pupil.get_full_name()

    class Meta(PupilExamGradebookSerializer.Meta):
        fields = (
            'source_kind', 'attempt_id', 'title', 'course_id', 'term_id',
            'pupil_id', 'pupil_name', 'status', 'total_score',
            'total_max_score', 'finished_at',
        )


class AnswerUpsertSerializer(serializers.Serializer):
    selected_option = serializers.PrimaryKeyRelatedField(
        queryset=AttemptOption.objects.all(),
        required=False,
    )
    essay_text = serializers.CharField(required=False, allow_blank=True)
    expected_version = serializers.IntegerField(min_value=0)

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - set(self.fields)
            if extra_fields:
                raise serializers.ValidationError({
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        item = self.context['attempt_item']
        if item.type == Question.Type.SINGLE_CHOICE:
            if 'selected_option' not in attrs or 'essay_text' in self.initial_data:
                raise serializers.ValidationError({
                    'selected_option': 'Single-choice javob uchun option majburiy.',
                })
        elif item.type == Question.Type.ESSAY:
            if 'essay_text' not in attrs or 'selected_option' in self.initial_data:
                raise serializers.ValidationError({
                    'essay_text': 'Essay javob uchun matn majburiy.',
                })
        else:
            raise serializers.ValidationError({'detail': 'Noma’lum savol turi.'})
        return attrs


class ExamItemInputListSerializer(serializers.ListSerializer):
    def validate(self, attrs):
        positions = [item['position'] for item in attrs]
        if len(positions) != len(set(positions)):
            raise serializers.ValidationError(
                'position qiymatlari bitta exam ichida takrorlanmasin.',
            )
        return attrs


class ExamItemInputSerializer(serializers.ModelSerializer):
    question = serializers.PrimaryKeyRelatedField(queryset=Question.objects.all())
    question_version = serializers.IntegerField(min_value=1, max_value=32767)
    points = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    position = serializers.IntegerField(min_value=0, max_value=32767)

    class Meta:
        model = ExamItem
        fields = ('question', 'question_version', 'points', 'position')
        list_serializer_class = ExamItemInputListSerializer

    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - set(self.fields)
            if extra_fields:
                raise serializers.ValidationError({
                    field: 'Bu maydon qabul qilinmaydi.'
                    for field in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        exam = self.context['exam']
        question = attrs['question']
        if question.course_id != exam.course_id:
            raise serializers.ValidationError({
                'question': 'Savol exam course’iga tegishli bo‘lishi kerak.'
            })
        if question.state == Question.State.ARCHIVED:
            raise serializers.ValidationError({
                'question': 'Arxivlangan savol examga biriktirilmaydi.'
            })
        if question.version != attrs['question_version']:
            raise serializers.ValidationError({
                'question_version': 'Yuborilgan version questionning joriy versioniga mos emas.'
            })
        return attrs


class ExamItemReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamItem
        fields = ('id', 'question', 'question_version', 'points', 'position')
        read_only_fields = fields
