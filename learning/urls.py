from django.urls import path

from learning.views import TopicViewSet

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
        'topics/<int:pk>/archive/',
        TopicViewSet.as_view({'post': 'archive'}),
        name='topic-archive',
    ),
    path(
        'topics/<int:pk>/publish/',
        TopicViewSet.as_view({'post': 'publish'}),
        name='topic-publish',
    ),
]
