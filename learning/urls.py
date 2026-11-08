from django.urls import path

from learning.views import (
    ResourceDetailView,
    ResourceListCreateView,
    ResourcePublishView,
    TopicCompletionView,
    TopicViewSet,
)

urlpatterns = [
    path(
        'courses/<int:course_pk>/topics/',
        TopicViewSet.as_view({'get': 'list'}),
        name='course-topic-list',
    ),
    path(
        'topics/',
        TopicViewSet.as_view({'post': 'create'}),
        name='topic-create',
    ),
    path(
        'topics/<int:pk>/',
        TopicViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'}),
        name='topic-detail',
    ),
    path(
        'topics/<int:pk>/completion/',
        TopicCompletionView.as_view(),
        name='topic-completion',
    ),
    path(
        'topics/<int:pk>/archive/',
        TopicViewSet.as_view({'post': 'archive'}),
        name='topic-archive',
    ),
    path(
        'topics/<int:pk>/publish/',
        TopicViewSet.as_view({'post': 'publish'}),
        name='topic-publish',
    ),
    path(
        'topics/<int:topic_pk>/resources/',
        ResourceListCreateView.as_view(),
        name='topic-resource-list-create',
    ),
    path(
        'resources/<int:pk>/',
        ResourceDetailView.as_view(),
        name='resource-detail',
    ),
    path(
        'resources/<int:pk>/publish/',
        ResourcePublishView.as_view(),
        name='resource-publish',
    ),
]
