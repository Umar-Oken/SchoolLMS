from rest_framework import serializers

from learning.models import Topic


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
        )
        read_only_fields = fields
