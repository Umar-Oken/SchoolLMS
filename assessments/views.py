from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.views import pupil_course_scope, teacher_course_scope
from assessments.models import Exam, ExamItem, Question
from assessments.serializers import (
    ExamItemInputSerializer,
    ExamItemReadSerializer,
    ExamMetadataSerializer,
    ExamMetadataUpdateSerializer,
    PupilExamCardSerializer,
    QuestionArchiveSerializer,
    TeacherQuestionSerializer,
    TeacherQuestionUpdateSerializer,
    TeacherExamPreviewSerializer,
)
from assessments.services import publish_exam


class IsQuestionBankTeacher(BasePermission):
    message = 'Savol banki faqat teacher uchun.'

    def has_permission(self, request, view):
        return request.user.role == User.Role.TEACHER


class IsExamWriterOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in ('GET', 'HEAD', 'OPTIONS') or (
            request.user.role == User.Role.TEACHER
        )


class QuestionViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    serializer_class = TeacherQuestionSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsQuestionBankTeacher,
    )

    def get_queryset(self):
        courses = teacher_course_scope(self.request.user, active_only=True)
        queryset = Question.objects.filter(
            course__in=courses,
            state__in=(Question.State.DRAFT, Question.State.PUBLISHED),
        ).select_related('course').prefetch_related('options')
        course_pk = self.kwargs.get('course_pk')
        if course_pk is not None:
            course = get_object_or_404(courses, pk=course_pk)
            queryset = queryset.filter(course=course)
        return queryset

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return TeacherQuestionUpdateSerializer
        return super().get_serializer_class()

    def perform_update(self, serializer):
        with transaction.atomic():
            question = self.get_queryset().select_for_update().get(
                pk=serializer.instance.pk,
            )
            serializer.instance = question
            serializer.save()

    @action(detail=True, methods=('post',))
    def archive(self, request, *args, **kwargs):
        question = self.get_object()
        serializer = QuestionArchiveSerializer(question, data={})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            TeacherQuestionSerializer(question).data,
            status=status.HTTP_200_OK,
        )


class ExamListCreateView(generics.ListCreateAPIView):
    serializer_class = ExamMetadataSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsExamWriterOrReadOnly,
    )

    def get_queryset(self):
        user = self.request.user
        queryset = Exam.objects.select_related('course', 'term')
        if user.role == User.Role.TEACHER:
            queryset = queryset.filter(
                course__in=teacher_course_scope(user, active_only=True),
            )
            if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
                queryset = queryset.filter(state=Exam.State.DRAFT)
            return queryset
        if user.role == User.Role.PUPIL:
            return queryset.filter(
                course__in=pupil_course_scope(user),
                state=Exam.State.PUBLISHED,
            )
        return queryset.none()

    def get_serializer_class(self):
        if self.request.method == 'GET' and getattr(
            self.request.user, 'role', None,
        ) == User.Role.PUPIL:
            return PupilExamCardSerializer
        return super().get_serializer_class()

    @extend_schema(
        summary='Pupil nazorat kartalari',
        description='Pupil response faqat exam metadata va holatini beradi; kalit, note va score yo‘q.',
        tags=['Assessments'],
        responses={200: PupilExamCardSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


class ExamDetailUpdateView(generics.RetrieveUpdateAPIView):
    serializer_class = ExamMetadataSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsExamWriterOrReadOnly,
    )

    def get_queryset(self):
        return ExamListCreateView.get_queryset(self)

    def get_serializer_class(self):
        if self.request.method == 'GET' and getattr(
            self.request.user, 'role', None,
        ) == User.Role.PUPIL:
            return PupilExamCardSerializer
        if self.request.method in ('PUT', 'PATCH'):
            return ExamMetadataUpdateSerializer
        return super().get_serializer_class()

    @extend_schema(
        summary='Pupil nazorat kartasi',
        description='Pupilga kalit, marking note, score yoki snapshot qaytarmaydi.',
        tags=['Assessments'],
        responses={200: PupilExamCardSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


class ExamItemReplaceView(generics.GenericAPIView):
    serializer_class = ExamItemInputSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsExamWriterOrReadOnly,
    )

    def get_queryset(self):
        return Exam.objects.filter(
            course__in=teacher_course_scope(self.request.user, active_only=True),
            state=Exam.State.DRAFT,
        )

    @extend_schema(
        summary='Draft nazorat savollarini almashtirish',
        description=(
            'Teacher draft Exam uchun bank question reference ro‘yxatini '
            'butunlay atomar almashtiradi. Bu hali immutable snapshot emas.'
        ),
        tags=['Assessments'],
        request=ExamItemInputSerializer(many=True),
        responses={200: ExamItemReadSerializer(many=True)},
    )
    def put(self, request, *args, **kwargs):
        with transaction.atomic():
            exam = get_object_or_404(
                self.get_queryset().select_for_update(),
                pk=self.kwargs['pk'],
            )
            serializer = ExamItemInputSerializer(
                data=request.data,
                many=True,
                context={'exam': exam},
            )
            serializer.is_valid(raise_exception=True)
            ExamItem.objects.filter(exam=exam).delete()
            ExamItem.objects.bulk_create([
                ExamItem(exam=exam, **item)
                for item in serializer.validated_data
            ])
        items = ExamItem.objects.filter(exam=exam).order_by('position', 'pk')
        return Response(ExamItemReadSerializer(items, many=True).data)


class ExamPublishView(generics.GenericAPIView):
    serializer_class = ExamMetadataSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsExamWriterOrReadOnly,
    )

    @extend_schema(
        summary='Draft nazoratni publish qilish',
        description=(
            'Draft item va bank ma’lumotlarini immutable server snapshotiga '
            'materialize qiladi. Kalit pupilga qaytmaydi.'
        ),
        tags=['Assessments'],
        request=None,
        responses={200: ExamMetadataSerializer},
    )
    def post(self, request, *args, **kwargs):
        exam = get_object_or_404(
            Exam.objects.filter(
                course__in=teacher_course_scope(request.user, active_only=True),
            ),
            pk=self.kwargs['pk'],
        )
        published_exam = publish_exam(exam_id=exam.pk, actor=request.user)
        return Response(ExamMetadataSerializer(published_exam).data)


class TeacherExamPreviewView(generics.RetrieveAPIView):
    serializer_class = TeacherExamPreviewSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsQuestionBankTeacher,
    )

    def get_queryset(self):
        return Exam.objects.filter(
            course__in=teacher_course_scope(self.request.user, active_only=True),
            state=Exam.State.PUBLISHED,
        ).prefetch_related('snapshot_items__options')

    @extend_schema(
        summary='Teacher uchun published exam preview',
        description='Faqat assigned teacherga publish snapshot va private kalit/marking note qaytaradi.',
        tags=['Assessments'],
        responses={200: TeacherExamPreviewSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)
