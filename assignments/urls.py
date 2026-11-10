from django.urls import path

from assignments.views import AssignmentCreateView, AssignmentDetailView

urlpatterns = [
    path(
        'assignments/',
        AssignmentCreateView.as_view(),
        name='assignment-create',
    ),
    path(
        'assignments/<int:pk>/',
        AssignmentDetailView.as_view(),
        name='assignment-detail',
    ),
]
