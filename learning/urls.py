from django.urls import path

from learning.views import TopicReadViewSet

urlpatterns = [
    path(
        'courses/<int:course_pk>/topics/',
        TopicReadViewSet.as_view({'get': 'list'}),
        name='course-topic-list',
    ),
    path(
        'topics/<int:pk>/',
        TopicReadViewSet.as_view({'get': 'retrieve'}),
        name='topic-detail',
    ),
]
