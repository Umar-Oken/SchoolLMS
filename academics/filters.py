from django_filters import rest_framework as filters

from academics.models import CourseOffering


class CourseFilter(filters.FilterSet):
    year = filters.NumberFilter(field_name='school_class__year_id')
    subject = filters.NumberFilter(field_name='subject_id')

    class Meta:
        model = CourseOffering
        fields = ()
