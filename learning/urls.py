from django.urls import path

from learning.views import (
    AnnouncementDetailView,
    AnnouncementListCreateView,
    AnnouncementPublishView,
    ResourceDetailView,
    ResourceListCreateView,
    ResourcePublishView,
    TopicCompletionView,
    TopicViewSet,
)

urlpatterns = [
    path(
        'announcements/',
        AnnouncementListCreateView.as_view(),
        name='announcement-create',
    ),
    path(
        'announcements/<int:pk>/',
        AnnouncementDetailView.as_view(),
        name='announcement-detail',
    ),
    path(
        'announcements/<int:pk>/publish/',
        AnnouncementPublishView.as_view(),
        name='announcement-publish',
    ),
    path(
        'courses/<int:course_pk>/announcements/',
        AnnouncementListCreateView.as_view(),
        name='course-announcement-list-create',
    ),
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
