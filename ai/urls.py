from django.urls import path

from ai.views import AIJobApproveView, AIJobRejectView, AIJobReviewView, AIJobStatusView, AIQuestionDraftRequestView


urlpatterns = [
    path(
        'ai/question-drafts/',
        AIQuestionDraftRequestView.as_view(),
        name='ai-question-draft-request',
    ),
    path('ai/jobs/<int:pk>/', AIJobStatusView.as_view(), name='ai-job-status'),
    path('ai/jobs/<int:pk>/review/', AIJobReviewView.as_view(), name='ai-job-review'),
    path('ai/jobs/<int:pk>/drafts/<int:draft_id>/', AIJobReviewView.as_view(), name='ai-draft-edit'),
    path('ai/jobs/<int:pk>/approve/', AIJobApproveView.as_view(), name='ai-job-approve'),
    path('ai/jobs/<int:pk>/reject/', AIJobRejectView.as_view(), name='ai-job-reject'),
]
