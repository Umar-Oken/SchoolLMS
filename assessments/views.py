from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.views import pupil_course_scope, teacher_course_scope
from assessments.models import Attempt, AttemptItem, AttemptResult, Exam, ExamItem, ExamSnapshotItem, Question
from assessments.serializers import (
    AnswerUpsertSerializer,
    AttemptFinishSerializer,
    PupilAttemptResultSerializer,
    PupilExamGradebookSerializer,
    ExamItemInputSerializer,
    ExamItemReadSerializer,
    ExamMetadataSerializer,
    ExamMetadataUpdateSerializer,
    PupilExamCardSerializer,
    PupilAttemptSerializer,
    PupilAnswerSerializer,
    QuestionArchiveSerializer,
    TeacherQuestionSerializer,
    TeacherQuestionUpdateSerializer,
    TeacherExamPreviewSerializer,
    TeacherAttemptGradeSerializer,
    TeacherAttemptGradingSerializer,
    TeacherEssayGradeInputSerializer,
    TeacherExamGradebookSerializer,
)
from assessments.services import (
    finalize_attempt,
    grade_essay_attempt_item,
    publish_exam,
    save_answer,
    start_attempt,
)


class IsQuestionBankTeacher(BasePermission):
    message = 'Savol banki faqat teacher uchun.'

    def has_permission(self, request, view):
        return request.user.role == User.Role.TEACHER


class IsPupil(BasePermission):
    message = 'Bu endpoint faqat pupil uchun.'

    def has_permission(self, request, view):
        return request.user.role == User.Role.PUPIL


class IsTeacher(BasePermission):
    message = 'Bu endpoint faqat teacher uchun.'

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


class AttemptStartView(generics.GenericAPIView):
    serializer_class = PupilAttemptSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    @extend_schema(
        summary='Pupil uchun nazorat urinishini boshlash',
        description='Faol urinish bo‘lsa shu urinishni key va baholash ma’lumotlarisiz qaytaradi.',
        tags=['Assessments'],
        request=None,
        responses={201: PupilAttemptSerializer},
    )
    def post(self, request, *args, **kwargs):
        attempt = start_attempt(exam_id=self.kwargs['pk'], pupil=request.user)
        attempt = type(attempt).objects.prefetch_related(
            'items__options', 'items__answer',
        ).get(pk=attempt.pk)
        return Response(PupilAttemptSerializer(attempt).data, status=status.HTTP_201_CREATED)


class AttemptDetailView(generics.RetrieveAPIView):
    serializer_class = PupilAttemptSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    def get_queryset(self):
        return Attempt.objects.filter(pupil=self.request.user).prefetch_related(
            'items__options', 'items__answer',
        )

    def get_object(self):
        attempt = super().get_object()
        if (
            attempt.state == Attempt.State.ACTIVE
            and timezone.now() >= attempt.expires_at
        ):
            finalize_attempt(attempt_id=attempt.pk, pupil=self.request.user)
            return self.get_queryset().get(pk=attempt.pk)
        return attempt

    @extend_schema(
        summary='Pupilning saqlangan nazorat urinishi',
        description='Kalitsiz savollar va oldingi javoblarning server ACK holatini qaytaradi.',
        tags=['Assessments'],
        responses={200: PupilAttemptSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


class AnswerUpsertView(generics.GenericAPIView):
    serializer_class = AnswerUpsertSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    def get_attempt(self):
        return get_object_or_404(
            Attempt.objects.filter(pupil=self.request.user),
            pk=self.kwargs['pk'],
        )

    @extend_schema(
        summary='Pupil javobini version bilan saqlash',
        description=(
            'expected_version serverdagi versiyaga teng bo‘lsa javob atomar '
            'saqlanadi va yangi server ACK qaytadi.'
        ),
        tags=['Assessments'],
        request=AnswerUpsertSerializer,
        responses={200: PupilAnswerSerializer},
    )
    def put(self, request, *args, **kwargs):
        attempt = self.get_attempt()
        item = get_object_or_404(
            AttemptItem.objects.filter(attempt=attempt),
            pk=self.kwargs['item_id'],
        )
        serializer = self.get_serializer(
            data=request.data,
            context={'attempt_item': item},
        )
        serializer.is_valid(raise_exception=True)
        answer = save_answer(
            attempt_id=attempt.pk,
            pupil=request.user,
            item_id=item.pk,
            validated_data=serializer.validated_data,
        )
        return Response(PupilAnswerSerializer(answer).data)


class AttemptFinishView(generics.GenericAPIView):
    serializer_class = AttemptFinishSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    @extend_schema(
        summary='Attemptni idempotent yakunlash',
        description=(
            'Faqat avval serverda saqlangan javoblardan objective natijani '
            'hisoblaydi; score va kalitni pupilga qaytarmaydi.'
        ),
        tags=['Assessments'],
        request=None,
        responses={200: AttemptFinishSerializer},
    )
    def post(self, request, *args, **kwargs):
        if request.data:
            raise ValidationError({'detail': 'Finish request body qabul qilmaydi.'})
        result = finalize_attempt(
            attempt_id=self.kwargs['pk'],
            pupil=request.user,
        )
        return Response(AttemptFinishSerializer(result).data)


class TeacherAttemptGradingView(generics.RetrieveAPIView):
    serializer_class = TeacherAttemptGradingSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    def get_queryset(self):
        essay_items = AttemptItem.objects.filter(
            type=Question.Type.ESSAY,
        ).prefetch_related('answer', 'manual_grade__audits')
        return Attempt.objects.filter(
            exam__course__in=teacher_course_scope(
                self.request.user, active_only=True, can_grade=True,
            ),
        ).prefetch_related(
            Prefetch('items', queryset=essay_items, to_attr='essay_items'),
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['max_scores'] = {
            item.pk: item.points
            for item in ExamSnapshotItem.objects.filter(
                exam__attempts__pk=self.kwargs['pk'],
            )
        }
        return context

    @extend_schema(
        summary='Teacher uchun essay grading tafsiloti',
        description='Faqat can_grade teacherga essay javoblari, current grade va audit qaytaradi.',
        tags=['Assessments'],
        responses={200: TeacherAttemptGradingSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


class TeacherEssayGradeView(generics.GenericAPIView):
    serializer_class = TeacherEssayGradeInputSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    def get_attempt(self):
        return get_object_or_404(
            Attempt.objects.filter(
                exam__course__in=teacher_course_scope(
                    self.request.user, active_only=True, can_grade=True,
                ),
            ),
            pk=self.kwargs['pk'],
        )

    @extend_schema(
        summary='Essay javobini baholash',
        description='can_grade teacher essay item uchun versionli ball va feedback saqlaydi.',
        tags=['Assessments'],
        request=TeacherEssayGradeInputSerializer,
        responses={200: TeacherAttemptGradeSerializer},
    )
    def put(self, request, *args, **kwargs):
        attempt = self.get_attempt()
        item = get_object_or_404(
            AttemptItem.objects.filter(
                attempt=attempt,
                type=Question.Type.ESSAY,
            ),
            pk=self.kwargs['item_id'],
        )
        snapshot = get_object_or_404(
            ExamSnapshotItem.objects.filter(
                exam=attempt.exam,
                type=Question.Type.ESSAY,
            ),
            pk=item.source_snapshot_item_id,
        )
        serializer = self.get_serializer(
            data=request.data,
            context={'max_score': snapshot.points},
        )
        serializer.is_valid(raise_exception=True)
        grade = grade_essay_attempt_item(
            attempt_id=attempt.pk,
            item_id=item.pk,
            grader=request.user,
            validated_data=serializer.validated_data,
        )
        return Response(TeacherAttemptGradeSerializer(grade).data)


class PupilAttemptResultView(generics.RetrieveAPIView):
    serializer_class = PupilAttemptResultSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    def get_object(self):
        now = timezone.now()
        return get_object_or_404(
            AttemptResult.objects.select_related('attempt__exam').filter(
                attempt_id=self.kwargs['pk'],
                attempt__pupil=self.request.user,
                attempt__exam__closes_at__lte=now,
                attempt__exam__result_release_at__lte=now,
            ),
        )

    @extend_schema(
        summary='Pupilning release qilingan exam natijasi',
        description='Faqat o‘z finalized resultini exam yopilib release vaqti kelgach qaytaradi.',
        tags=['Assessments'],
        responses={200: PupilAttemptResultSerializer},
    )
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


class PupilExamGradebookView(generics.ListAPIView):
    serializer_class = PupilExamGradebookSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsPupil)

    def get_queryset(self):
        now = timezone.now()
        return AttemptResult.objects.filter(
            attempt__pupil=self.request.user,
            status=AttemptResult.Status.FINALIZED,
            attempt__exam__closes_at__lte=now,
            attempt__exam__result_release_at__lte=now,
        ).select_related('attempt__exam__course', 'attempt__exam__term')


class TeacherExamGradebookView(generics.ListAPIView):
    serializer_class = TeacherExamGradebookSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission, IsTeacher)

    def get_queryset(self):
        course = get_object_or_404(
            teacher_course_scope(
                self.request.user, active_only=True, can_grade=True,
            ),
            pk=self.kwargs['course_pk'],
        )
        return AttemptResult.objects.filter(
            attempt__exam__course=course,
        ).select_related('attempt__exam__course', 'attempt__exam__term', 'attempt__pupil')
