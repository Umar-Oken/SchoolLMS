from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import NotFound
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from assignments.time import assignment_time_status
from academics.views import teacher_course_scope
from learning.models import Topic

from assignments.models import (
    Assignment,
    Criterion,
    CriterionScore,
    Grade,
    Level,
)


class AssignmentSerializer(serializers.ModelSerializer):
    topic = serializers.PrimaryKeyRelatedField(
        queryset=Topic.objects.all(),
    )
    max_score = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    max_attempts = serializers.IntegerField(min_value=1, max_value=32767)
    state = serializers.ChoiceField(
        choices=Assignment.State.choices,
        read_only=True,
    )

    class Meta:
        model = Assignment
        fields = (
            'id',
            'topic',
            'title',
            'instructions',
            'available_at',
            'due_at',
            'closes_at',
            'max_score',
            'max_attempts',
            'state',
            'published_at',
            'rubric_version',
        )
        read_only_fields = (
            'id',
            'state',
            'published_at',
            'rubric_version',
        )

    def to_internal_value(self, data):
        allowed_fields = {
            'topic',
            'title',
            'instructions',
            'available_at',
            'due_at',
            'closes_at',
            'max_score',
            'max_attempts',
        }
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate_topic(self, topic):
        request = self.context['request']
        if not teacher_course_scope(
            request.user,
            active_only=True,
        ).filter(
            pk=topic.course_id,
        ).exists() or topic.archived_at is not None:
            raise NotFound()
        return topic

    def validate(self, attrs):
        available_at = attrs['available_at']
        due_at = attrs['due_at']
        closes_at = attrs['closes_at']
        errors = {}
        if available_at > due_at:
            errors['due_at'] = (
                'available_at qiymatidan oldin bo‘lishi mumkin emas.'
            )
        if due_at > closes_at:
            errors['closes_at'] = (
                'due_at qiymatidan oldin bo‘lishi mumkin emas.'
            )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def to_representation(self, instance):
        return AssignmentReadSerializer(
            instance,
            context=self.context,
        ).data


class AssignmentReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = (
            'id',
            'topic',
            'title',
            'instructions',
            'available_at',
            'due_at',
            'closes_at',
            'max_score',
            'max_attempts',
            'state',
            'published_at',
            'rubric_version',
        )
        read_only_fields = fields


class StrictInputSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if hasattr(data, 'keys'):
            allowed_fields = set(self.fields)
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)


class RubricLevelInputSerializer(StrictInputSerializer):
    id = serializers.IntegerField(min_value=1, required=False)
    description = serializers.CharField()
    points = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0'),
    )
    position = serializers.IntegerField(min_value=0, max_value=32767)


class RubricCriterionInputSerializer(StrictInputSerializer):
    id = serializers.IntegerField(min_value=1, required=False)
    label = serializers.CharField(max_length=200)
    max_points = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0.01'),
    )
    position = serializers.IntegerField(min_value=0, max_value=32767)
    levels = RubricLevelInputSerializer(many=True)

    def validate(self, attrs):
        max_points = attrs['max_points']
        for index, level in enumerate(attrs['levels']):
            if level['points'] > max_points:
                raise serializers.ValidationError({
                    'levels': {
                        index: {
                            'points': (
                                'Level points criterion max_points '
                                'qiymatidan oshmasligi kerak.'
                            )
                        }
                    }
                })
        level_ids = [
            level['id'] for level in attrs['levels'] if 'id' in level
        ]
        if len(level_ids) != len(set(level_ids)):
            raise serializers.ValidationError({
                'levels': 'Level ID bir rubric ichida takrorlanmasin.'
            })
        positions = [level['position'] for level in attrs['levels']]
        if len(positions) != len(set(positions)):
            raise serializers.ValidationError({
                'levels': 'Level position qiymatlari takrorlanmasin.'
            })
        return attrs


class RubricReplacementSerializer(StrictInputSerializer):
    criteria = RubricCriterionInputSerializer(many=True)

    def validate(self, attrs):
        criteria = attrs['criteria']
        criterion_ids = [
            criterion['id'] for criterion in criteria
            if 'id' in criterion
        ]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise serializers.ValidationError({
                'criteria': 'Criterion ID bir rubric ichida takrorlanmasin.'
            })
        positions = [criterion['position'] for criterion in criteria]
        if len(positions) != len(set(positions)):
            raise serializers.ValidationError({
                'criteria': 'Criterion position qiymatlari takrorlanmasin.'
            })

        assignment = self.context['assignment']
        existing_criteria = {
            criterion.pk: criterion
            for criterion in Criterion.objects.filter(
                assignment=assignment,
            ).prefetch_related('levels')
        }
        for criterion in criteria:
            criterion_id = criterion.get('id')
            current_criterion = existing_criteria.get(criterion_id)
            if criterion_id is not None and current_criterion is None:
                raise serializers.ValidationError({
                    'criteria': (
                        f"Criterion ID {criterion_id} bu assignmentga tegishli emas."
                    )
                })
            existing_levels = (
                {
                    level.pk: level
                    for level in current_criterion.levels.all()
                }
                if current_criterion is not None
                else {}
            )
            for level in criterion['levels']:
                level_id = level.get('id')
                if level_id is not None and (
                    level_id not in existing_levels
                ):
                    raise serializers.ValidationError({
                        'criteria': (
                            f"Level ID {level_id} berilgan criterionga "
                            'tegishli emas.'
                        )
                    })
        return attrs


class RubricLevelReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Level
        fields = ('id', 'description', 'points', 'position')


class RubricCriterionReadSerializer(serializers.ModelSerializer):
    levels = RubricLevelReadSerializer(many=True, read_only=True)

    class Meta:
        model = Criterion
        fields = ('id', 'label', 'max_points', 'position', 'levels')


class RubricReadSerializer(serializers.ModelSerializer):
    criteria = RubricCriterionReadSerializer(many=True, read_only=True)

    class Meta:
        model = Assignment
        fields = ('id', 'criteria')


class PupilAssignmentReadSerializer(serializers.ModelSerializer):
    course = serializers.IntegerField(source='topic.course_id', read_only=True)
    rubric = RubricCriterionReadSerializer(
        source='criteria',
        many=True,
        read_only=True,
    )
    availability_status = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = (
            'id',
            'course',
            'title',
            'instructions',
            'available_at',
            'due_at',
            'closes_at',
            'max_score',
            'max_attempts',
            'rubric',
            'availability_status',
            'can_submit',
        )
        read_only_fields = fields

    def to_representation(self, instance):
        self._time_status = assignment_time_status(instance)
        return super().to_representation(instance)

    @extend_schema_field(OpenApiTypes.STR)
    def get_availability_status(self, instance):
        return self._time_status['availability_status']

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_can_submit(self, instance):
        return self._time_status['can_submit']


class MyDraftReadSerializer(serializers.Serializer):
    submission_id = serializers.IntegerField(allow_null=True)
    assignment = serializers.IntegerField()
    draft_text = serializers.CharField(allow_blank=True)
    draft_version = serializers.IntegerField(min_value=0)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
    )


class MyDraftUpdateSerializer(StrictInputSerializer):
    draft_text = serializers.CharField(allow_blank=True, trim_whitespace=False)
    draft_version = serializers.IntegerField(min_value=0)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        max_length=3,
        required=False,
    )

    def validate_attachment_ids(self, attachment_ids):
        if len(attachment_ids) != len(set(attachment_ids)):
            raise serializers.ValidationError(
                'Fayl ID’lari takrorlanmasligi kerak.'
            )
        return attachment_ids


class SubmitWorkSerializer(StrictInputSerializer):
    draft_version = serializers.IntegerField(min_value=0)


class SubmitReceiptSerializer(serializers.Serializer):
    submission_id = serializers.IntegerField()
    revision_id = serializers.IntegerField()
    revision_number = serializers.IntegerField()
    draft_version = serializers.IntegerField()
    submitted_at = serializers.DateTimeField()
    is_late = serializers.BooleanField()
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
    )


class ReturnWorkSerializer(StrictInputSerializer):
    reason = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        max_length=2000,
    )


class ReturnReceiptSerializer(serializers.Serializer):
    submission_id = serializers.IntegerField()
    revision_id = serializers.IntegerField()
    revision_number = serializers.IntegerField()
    draft_version = serializers.IntegerField()
    returned_at = serializers.DateTimeField()
    reason = serializers.CharField()


class CriterionScoreInputSerializer(StrictInputSerializer):
    criterion_id = serializers.IntegerField(min_value=1)
    points = serializers.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0'),
    )
    feedback = serializers.CharField(
        allow_blank=True,
        trim_whitespace=False,
        required=False,
        default='',
    )


class GradeDraftWriteSerializer(StrictInputSerializer):
    version = serializers.IntegerField(min_value=0)
    feedback = serializers.CharField(allow_blank=True, trim_whitespace=False)
    criterion_scores = CriterionScoreInputSerializer(many=True)

    def validate_criterion_scores(self, scores):
        criteria = self.context['criteria']
        expected = {criterion.pk: criterion for criterion in criteria}
        submitted_ids = [score['criterion_id'] for score in scores]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise serializers.ValidationError(
                'Har bir criterion faqat bir marta yuborilsin.'
            )
        if set(submitted_ids) != set(expected):
            raise serializers.ValidationError(
                'Barcha rubric criterion’lari uchun aynan bittadan ball kerak.'
            )
        errors = {}
        for index, score in enumerate(scores):
            criterion = expected[score['criterion_id']]
            if score['points'] > criterion.max_points:
                errors[index] = {
                    'points': (
                        f'Ball {criterion.max_points} qiymatidan oshmasligi '
                        'kerak.'
                    )
                }
        if errors:
            raise serializers.ValidationError(errors)
        return scores


class CriterionScoreReadSerializer(serializers.ModelSerializer):
    criterion_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = CriterionScore
        fields = ('criterion_id', 'points', 'feedback')
        read_only_fields = fields


class GradeDraftReadSerializer(serializers.ModelSerializer):
    revision_id = serializers.IntegerField(read_only=True)
    criterion_scores = CriterionScoreReadSerializer(many=True, read_only=True)

    class Meta:
        model = Grade
        fields = (
            'id',
            'revision_id',
            'state',
            'total',
            'feedback',
            'version',
            'published_at',
            'criterion_scores',
        )
        read_only_fields = fields


class GradePublishSerializer(StrictInputSerializer):
    grade_version = serializers.IntegerField(min_value=0)
    revision_version = serializers.IntegerField(min_value=0)


class GradeRegradeSerializer(StrictInputSerializer):
    expected_version = serializers.IntegerField(min_value=1)
    criterion_scores = CriterionScoreInputSerializer(many=True)
    reason = serializers.CharField(
        allow_blank=False,
        trim_whitespace=True,
        min_length=1,
        max_length=2000,
    )

    def validate_criterion_scores(self, scores):
        criteria = self.context['criteria']
        expected = {criterion.pk: criterion for criterion in criteria}
        submitted_ids = [score['criterion_id'] for score in scores]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise serializers.ValidationError(
                'Har bir criterion faqat bir marta yuborilsin.'
            )
        if set(submitted_ids) != set(expected):
            raise serializers.ValidationError(
                'Barcha rubric criterion’lari uchun bittadan ball yuboring.'
            )
        errors = {}
        for index, score in enumerate(scores):
            criterion = expected[score['criterion_id']]
            if score['points'] > criterion.max_points:
                errors[index] = {
                    'points': (
                        f'Ball {criterion.max_points} qiymatidan oshmasligi '
                        'kerak.'
                    )
                }
        if errors:
            raise serializers.ValidationError(errors)
        return scores


class PublishedGradeReadSerializer(serializers.ModelSerializer):
    revision_id = serializers.IntegerField(read_only=True)
    criterion_scores = CriterionScoreReadSerializer(many=True, read_only=True)

    class Meta:
        model = Grade
        fields = (
            'id',
            'revision_id',
            'state',
            'total',
            'feedback',
            'version',
            'published_at',
            'criterion_scores',
        )
        read_only_fields = fields
