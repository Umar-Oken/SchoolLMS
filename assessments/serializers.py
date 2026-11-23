from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from academics.models import CourseOffering
from academics.views import teacher_course_scope
from assessments.models import Exam, ExamItem, Option, Question
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
