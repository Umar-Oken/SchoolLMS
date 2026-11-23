from django.urls import path

from assessments.views import (
    ExamDetailUpdateView,
    ExamItemReplaceView,
    ExamListCreateView,
    ExamPublishView,
    TeacherExamPreviewView,
    QuestionViewSet,
)


urlpatterns = [
    path('exams/', ExamListCreateView.as_view(), name='exam-list-create'),
    path('exams/<int:pk>/', ExamDetailUpdateView.as_view(), name='exam-detail-update'),
    path('exams/<int:pk>/items/', ExamItemReplaceView.as_view(), name='exam-item-replace'),
    path('exams/<int:pk>/publish/', ExamPublishView.as_view(), name='exam-publish'),
    path('exams/<int:pk>/preview/', TeacherExamPreviewView.as_view(), name='exam-preview'),
    path(
        'courses/<int:course_pk>/questions/',
        QuestionViewSet.as_view({'get': 'list'}),
        name='course-question-list',
    ),
    path(
        'questions/',
        QuestionViewSet.as_view({'post': 'create'}),
        name='question-create',
    ),
    path(
        'questions/<int:pk>/',
        QuestionViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'}),
        name='question-detail',
    ),
    path(
        'questions/<int:pk>/archive/',
        QuestionViewSet.as_view({'post': 'archive'}),
        name='question-archive',
    ),
]
