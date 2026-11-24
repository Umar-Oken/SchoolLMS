from django.urls import path

from assessments.views import (
    AnswerUpsertView,
    AttemptDetailView,
    AttemptFinishView,
    PupilAttemptResultView,
    PupilExamGradebookView,
    TeacherAttemptGradingView,
    TeacherEssayGradeView,
    TeacherExamGradebookView,
    ExamDetailUpdateView,
    ExamItemReplaceView,
    ExamListCreateView,
    ExamPublishView,
    TeacherExamPreviewView,
    AttemptStartView,
    QuestionViewSet,
)


urlpatterns = [
    path('exams/', ExamListCreateView.as_view(), name='exam-list-create'),
    path('exams/<int:pk>/', ExamDetailUpdateView.as_view(), name='exam-detail-update'),
    path('exams/<int:pk>/items/', ExamItemReplaceView.as_view(), name='exam-item-replace'),
    path('exams/<int:pk>/publish/', ExamPublishView.as_view(), name='exam-publish'),
    path('exams/<int:pk>/preview/', TeacherExamPreviewView.as_view(), name='exam-preview'),
    path('exams/<int:pk>/attempts/', AttemptStartView.as_view(), name='attempt-start'),
    path('attempts/<int:pk>/', AttemptDetailView.as_view(), name='attempt-detail'),
    path('attempts/<int:pk>/finish/', AttemptFinishView.as_view(), name='attempt-finish'),
    path('attempts/<int:pk>/result/', PupilAttemptResultView.as_view(), name='attempt-result'),
    path('attempts/<int:pk>/grading/', TeacherAttemptGradingView.as_view(), name='attempt-grading'),
    path(
        'attempts/<int:pk>/items/<int:item_id>/grade/',
        TeacherEssayGradeView.as_view(),
        name='attempt-essay-grade',
    ),
    path('me/exam-gradebook/', PupilExamGradebookView.as_view(), name='pupil-exam-gradebook'),
    path(
        'courses/<int:course_pk>/exam-gradebook/',
        TeacherExamGradebookView.as_view(),
        name='teacher-exam-gradebook',
    ),
    path(
        'attempts/<int:pk>/answers/<int:item_id>/',
        AnswerUpsertView.as_view(),
        name='attempt-answer-upsert',
    ),
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
