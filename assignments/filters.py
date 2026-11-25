from django_filters import rest_framework as filters

from assignments.models import Assignment


class AssignmentFilter(filters.FilterSet):
    course = filters.NumberFilter(field_name='topic__course_id')
    term = filters.NumberFilter(field_name='topic__term_id')
    due_after = filters.IsoDateTimeFilter(field_name='due_at', lookup_expr='gte')
    due_before = filters.IsoDateTimeFilter(field_name='due_at', lookup_expr='lte')

    class Meta:
        model = Assignment
        fields = ()
