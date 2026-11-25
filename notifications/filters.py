from django_filters import rest_framework as filters

from notifications.models import Notification


class NotificationFilter(filters.FilterSet):
    kind = filters.CharFilter()
    read = filters.BooleanFilter(field_name='read_at', lookup_expr='isnull', exclude=True)
    created_after = filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_before = filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='lte')

    class Meta:
        model = Notification
        fields = ()
