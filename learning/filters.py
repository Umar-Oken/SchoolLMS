from django_filters import rest_framework as filters

from learning.models import Topic


class TopicFilter(filters.FilterSet):
    course = filters.NumberFilter(field_name='course_id')
    term = filters.NumberFilter(field_name='term_id')
    status = filters.ChoiceFilter(field_name='state', choices=Topic.State.choices)

    class Meta:
        model = Topic
        fields = ()
