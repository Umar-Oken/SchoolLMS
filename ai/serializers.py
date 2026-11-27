from rest_framework import serializers

from ai.models import AIJob, AIJobResultDraft
from ai.services import create_ai_job
from learning.models import Resource


class AIJobResultDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIJobResultDraft
        fields = (
            'id',
            'position',
            'question_text',
            'choices',
            'correct_choice_index',
            'explanation',
            'citation',
            'created_at',
            'approved_question',
        )
        read_only_fields = fields


class AIJobReadSerializer(serializers.ModelSerializer):
    result_drafts = AIJobResultDraftSerializer(many=True, read_only=True)

    class Meta:
        model = AIJob
        fields = (
            'id',
            'actor',
            'course',
            'resource',
            'resource_version',
            'requested_question_count',
            'state',
            'prompt_version',
            'draft_version',
            'provider_model',
            'tokens',
            'error',
            'created_at',
            'updated_at',
            'result_drafts',
        )
        read_only_fields = fields


class AIJobCreateSerializer(serializers.Serializer):
    """Future API input: actor/course/version are derived server-side."""

    resource = serializers.PrimaryKeyRelatedField(queryset=Resource.objects.all())
    prompt_version = serializers.IntegerField(min_value=1, default=1)

    def to_internal_value(self, data):
        allowed_fields = {'resource', 'prompt_version'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon server tomonidan belgilanadi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def create(self, validated_data):
        request = self.context['request']
        return create_ai_job(
            actor=request.user,
            resource=validated_data['resource'],
            prompt_version=validated_data['prompt_version'],
        )


class AIQuestionDraftRequestSerializer(serializers.Serializer):
    resource = serializers.PrimaryKeyRelatedField(queryset=Resource.objects.all())
    resource_version = serializers.IntegerField(min_value=1)

    def to_internal_value(self, data):
        allowed_fields = {'resource', 'resource_version'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.' for name in extra_fields
                })
        return super().to_internal_value(data)


class AIDraftReviewSerializer(serializers.Serializer):
    draft_version = serializers.IntegerField(min_value=1)
    question_text = serializers.CharField(trim_whitespace=True, allow_blank=False, required=False)
    choices = serializers.ListField(child=serializers.CharField(allow_blank=False), min_length=2, required=False)
    correct_choice_index = serializers.IntegerField(min_value=0, required=False)
    explanation = serializers.CharField(trim_whitespace=True, allow_blank=False, required=False)

    def validate(self, attrs):
        if {'question_text', 'choices', 'correct_choice_index', 'explanation'} & set(attrs) and len(attrs) != 5:
            raise serializers.ValidationError('Edit barcha draft maydonlarini talab qiladi.')
        if 'choices' in attrs and attrs['correct_choice_index'] >= len(attrs['choices']):
            raise serializers.ValidationError({'correct_choice_index': 'Invalid choice index.'})
        return attrs


class AIJobAcceptedSerializer(serializers.Serializer):
    job_id = serializers.IntegerField(read_only=True)
    status = serializers.ChoiceField(choices=AIJob.State.choices, read_only=True)
    status_url = serializers.CharField(read_only=True)
    request_id = serializers.CharField(read_only=True)
