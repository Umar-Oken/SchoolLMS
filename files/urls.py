from django.urls import path

from files.views import AttachmentDownloadView, AttachmentUploadView

urlpatterns = [
    path('attachments/', AttachmentUploadView.as_view(), name='attachment-upload'),
    path(
        'attachments/<int:pk>/download/',
        AttachmentDownloadView.as_view(),
        name='attachment-download',
    ),
]
