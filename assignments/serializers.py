from decimal import Decimal

from rest_framework import serializers
from rest_framework.exceptions import NotFound

from academics.views import teacher_course_scope
from learning.models import Topic

from assignments.models import Assignment


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
        )
        read_only_fields = ('id', 'state')

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
        )
        read_only_fields = fields
