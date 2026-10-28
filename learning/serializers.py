from rest_framework import serializers
from rest_framework.exceptions import NotFound

from academics.models import CourseOffering, Term
from academics.views import teacher_course_scope
from learning.models import Topic


class TopicCreateSerializer(serializers.ModelSerializer):
    course = serializers.PrimaryKeyRelatedField(
        queryset=CourseOffering.objects.all(),
    )
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())

    class Meta:
        model = Topic
        fields = (
            'id',
            'course',
            'term',
            'title',
            'objective',
            'order',
            'state',
            'published_at',
        )
        read_only_fields = ('id', 'state', 'published_at')

    def to_internal_value(self, data):
        allowed_fields = {'course', 'term', 'title', 'objective', 'order'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydon qabul qilinmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate_course(self, course):
        user = self.context['request'].user
        if not teacher_course_scope(user, active_only=True).filter(
            pk=course.pk,
        ).exists():
            raise NotFound()
        return course

    def validate(self, attrs):
        course = attrs['course']
        term = attrs['term']
        if term.year_id != course.school_class.year_id:
            raise serializers.ValidationError({
                'term': 'Term and course must belong to the same academic year.'
            })
        return attrs


class TopicUpdateSerializer(serializers.ModelSerializer):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())

    class Meta:
        model = Topic
        fields = ('id', 'term', 'title', 'objective', 'order')
        read_only_fields = ('id',)

    def to_internal_value(self, data):
        allowed_fields = {'term', 'title', 'objective', 'order'}
        if hasattr(data, 'keys'):
            extra_fields = set(data.keys()) - allowed_fields
            if extra_fields:
                raise serializers.ValidationError({
                    name: 'Bu maydonni o‘zgartirib bo‘lmaydi.'
                    for name in extra_fields
                })
        return super().to_internal_value(data)

    def validate(self, attrs):
        term = attrs.get('term', self.instance.term)
        if term.year_id != self.instance.course.school_class.year_id:
            raise serializers.ValidationError({
                'term': 'Term and course must belong to the same academic year.'
            })
        return attrs


class TopicReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = (
            'id',
            'course',
            'term',
            'title',
            'objective',
            'order',
            'state',
            'published_at',
            'archived_at',
        )
        read_only_fields = fields
