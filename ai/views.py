from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from ai.models import AIJob
from ai.serializers import (
    AIJobAcceptedSerializer,
    AIJobReadSerializer,
    AIQuestionDraftRequestSerializer,
    AIDraftReviewSerializer,
)
from ai.services import approve_ai_job, edit_ai_draft, reject_ai_job, request_question_drafts
from root.openapi import common_error_responses, error_response


class IsTeacher(BasePermission):
    message = 'Bu endpoint faqat teacher uchun.'

    def has_permission(self, request, view):
        return request.user.role == User.Role.TEACHER


class AIQuestionDraftRequestView(GenericAPIView):
    serializer_class = AIQuestionDraftRequestSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    @extend_schema(
        summary='PDF resource asosida 5 AI savol qoralamasini navbatga qo‘yish',
        description=(
            'Faqat faol biriktirilgan teacherning clean, published PDF resource '
            'va aynan uning current versioni qabul qilinadi. Javob 202 — savollar '
            'tayyor emas, faqat queued job yaratildi. Idempotency-Key retry bir xil '
            'jobni qaytaradi va daily quota qayta rezerv qilinmaydi.'
        ),
        tags=['AI'],
        request=AIQuestionDraftRequestSerializer,
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key', type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER, required=True,
            ),
        ],
        responses={
            202: AIJobAcceptedSerializer,
            429: error_response(
                'Bugungi AI job limiti tugagan.',
                {'code': 'ai_daily_quota_exceeded'},
                name='429 — AI daily quota',
            ),
            **common_error_responses(),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job, _created = request_question_drafts(
            actor=request.user,
            resource_id=serializer.validated_data['resource'].pk,
            resource_version=serializer.validated_data['resource_version'],
            idempotency_key=request.headers.get('Idempotency-Key', ''),
            daily_limit=getattr(settings, 'AI_DAILY_JOB_LIMIT', 10),
        )
        return Response({
            'job_id': job.pk, 'status': job.state,
            'status_url': request.build_absolute_uri(reverse('ai-job-status', kwargs={'pk': job.pk})),
            'request_id': getattr(request, 'request_id', ''),
        }, status=status.HTTP_202_ACCEPTED)


class AIJobReviewView(GenericAPIView):
    serializer_class = AIDraftReviewSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    def get_object(self):
        return get_object_or_404(AIJob.objects.prefetch_related('result_drafts'), pk=self.kwargs['pk'], actor=self.request.user)

    def get(self, request, *args, **kwargs):
        return Response(AIJobReadSerializer(self.get_object()).data)

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft, job = edit_ai_draft(actor=request.user, job_id=kwargs['pk'], draft_id=kwargs['draft_id'], draft_version=serializer.validated_data.pop('draft_version'), data=serializer.validated_data)
        return Response({'draft': AIJobReadSerializer(job).data, 'draft_id': draft.pk})


class AIJobApproveView(GenericAPIView):
    serializer_class = AIDraftReviewSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = approve_ai_job(actor=request.user, job_id=kwargs['pk'], draft_version=serializer.validated_data['draft_version'])
        return Response({'question_ids': ids, 'state': AIJob.State.APPROVED})


class AIJobRejectView(AIJobApproveView):
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = reject_ai_job(actor=request.user, job_id=kwargs['pk'], draft_version=serializer.validated_data['draft_version'])
        return Response({'state': job.state})


class AIJobStatusView(GenericAPIView):
    serializer_class = AIJobReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    def get_queryset(self):
        return AIJob.objects.filter(actor=self.request.user).select_related(
            'course', 'resource',
        ).prefetch_related('result_drafts')

    @extend_schema(
        summary='O‘z AI job statusini ko‘rish',
        description=(
            'Faqat jobni yaratgan teacher ko‘radi. queued/running holati savol '
            'tayyorligini bildirmaydi; review natija inson ko‘rigi uchun draftdir.'
        ),
        tags=['AI'],
        responses={200: AIJobReadSerializer, **common_error_responses()},
    )
    def get(self, request, *args, **kwargs):
        job = get_object_or_404(self.get_queryset(), pk=kwargs['pk'])
        return Response(self.get_serializer(job).data)
