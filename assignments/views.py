from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiTypes,
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.views import pupil_course_scope, teacher_course_scope
from assignments.models import (
    Assignment,
    Criterion,
    Grade,
    Submission,
    SubmissionRevision,
)
from assignments.gradebook_serializers import PupilGradebookEntrySerializer
from assignments.teacher_gradebook_serializers import (
    TeacherSubmissionQueueSerializer,
)
from assignments.serializers import (
    AssignmentReadSerializer,
    AssignmentSerializer,
    GradeDraftReadSerializer,
    GradeDraftWriteSerializer,
    GradePublishSerializer,
    GradeRegradeSerializer,
    MyDraftReadSerializer,
    MyDraftUpdateSerializer,
    PupilAssignmentReadSerializer,
    RubricReadSerializer,
    RubricReplacementSerializer,
    PublishedGradeReadSerializer,
    ReturnReceiptSerializer,
    ReturnWorkSerializer,
    SubmitReceiptSerializer,
    SubmitWorkSerializer,
)
from assignments.services import (
    submit_work,
    publish_assignment,
    replace_assignment_rubric,
    save_submission_draft,
    return_work,
    save_grade_draft,
    publish_grade,
    regrade_submission,
)
from assignments.selectors import (
    pupil_gradebook_queryset,
    teacher_course_scope_queryset,
    teacher_submission_queue_queryset,
)
from root.openapi import common_error_responses


class IsTeacher(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'role', None) == User.Role.TEACHER


class IsAssignmentReader(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'role', None) in (
            User.Role.PUPIL,
            User.Role.TEACHER,
        )


class IsPupil(BasePermission):
    def has_permission(self, request, view):
        return getattr(request.user, 'role', None) == User.Role.PUPIL


@extend_schema_view(
    get=extend_schema(
        summary='O‘quvchining published vazifalar ro‘yxati',
        description=(
            'Faqat o‘quvchining faol enrollment’i bor kurslardagi published '
            'topic va assignment ko‘rinadi. available_at kelmagan vazifa '
            'metadata ham qaytadi, ammo can_submit false bo‘ladi.'
        ),
        tags=['Assignments'],
        parameters=[
            OpenApiParameter(
                name='course',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Faqat shu faol enrollment kursidagi vazifalar.',
            ),
        ],
        responses={
            200: PupilAssignmentReadSerializer(many=True),
            **common_error_responses(),
        },
    ),
    post=extend_schema(
        summary='Draft vazifa yaratish',
        description=(
            'Faol ravishda biriktirilgan kursdagi o‘z mavzusiga draft vazifa '
            'yaratadi. State server tomonidan draft qilib belgilanadi.'
        ),
        tags=['Assignments'],
        request=AssignmentSerializer,
        responses={
            201: AssignmentReadSerializer,
            **common_error_responses(),
        },
        examples=[
            OpenApiExample(
                'Draft vazifa yaratish',
                value={
                    'topic': 42,
                    'title': 'Kasrlarni taqqoslash',
                    'instructions': 'Javobni tushuntirib yozing.',
                    'available_at': '2026-11-10T09:00:00Z',
                    'due_at': '2026-11-14T12:00:00Z',
                    'closes_at': '2026-11-15T12:00:00Z',
                    'max_score': '10.00',
                    'max_attempts': 2,
                },
                request_only=True,
            ),
            OpenApiExample(
                'Yaratilgan draft vazifa',
                value={
                    'id': 1,
                    'topic': 42,
                    'title': 'Kasrlarni taqqoslash',
                    'instructions': 'Javobni tushuntirib yozing.',
                    'available_at': '2026-11-10T09:00:00Z',
                    'due_at': '2026-11-14T12:00:00Z',
                    'closes_at': '2026-11-15T12:00:00Z',
                    'max_score': '10.00',
                    'max_attempts': 2,
                    'state': 'draft',
                },
                response_only=True,
            ),
        ],
    ),
)
class AssignmentCreateView(generics.ListCreateAPIView):
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsAssignmentReader,
    )

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == 'GET':
            permissions[-1] = IsPupil()
        elif self.request.method == 'POST':
            permissions[-1] = IsTeacher()
        return permissions

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return PupilAssignmentReadSerializer
        return AssignmentSerializer

    def get_queryset(self) -> QuerySet[Assignment]:
        if self.request.method != 'GET':
            return Assignment.objects.none()
        courses = pupil_course_scope(self.request.user, active_only=True)
        course_id = self.request.query_params.get('course')
        if course_id is not None:
            try:
                course_id = serializers.IntegerField(min_value=1).run_validation(
                    course_id,
                )
            except ValidationError as exc:
                raise ValidationError({'course': exc.detail}) from exc
            courses = courses.filter(pk=course_id)

        return (
            Assignment.objects.filter(
                topic__course_id__in=courses.values('pk'),
                topic__state='published',
                topic__archived_at__isnull=True,
                state=Assignment.State.PUBLISHED,
            )
            .select_related('topic')
            .prefetch_related('criteria__levels')
        )

    def perform_create(self, serializer):
        serializer.save(state=Assignment.State.DRAFT)


@extend_schema_view(
    get=extend_schema(
        summary='Draft vazifa tafsiloti',
        description=(
            'Teacher o‘z faol kursidagi draftini, pupil esa faol enrollment’i '
            'bor kursdagi published topic va assignmentni ko‘radi. Pupil '
            'available_at gacha ham metadata ko‘rishi mumkin, ammo topshira olmaydi.'
        ),
        tags=['Assignments'],
        responses={
            200: PolymorphicProxySerializer(
                component_name='AssignmentVisibleDetail',
                serializers=(
                    AssignmentReadSerializer,
                    PupilAssignmentReadSerializer,
                ),
                resource_type_field_name=None,
            ),
            **common_error_responses(),
        },
    ),
)
class AssignmentDetailView(generics.RetrieveAPIView):
    serializer_class = AssignmentReadSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsAssignmentReader,
    )

    def get_serializer_class(self):
        if self.request.user.role == User.Role.PUPIL:
            return PupilAssignmentReadSerializer
        return AssignmentReadSerializer

    def get_queryset(self) -> QuerySet[Assignment]:
        user = self.request.user
        if user.role == User.Role.PUPIL:
            courses = pupil_course_scope(user, active_only=True)
            return (
                Assignment.objects.filter(
                    topic__course_id__in=courses.values('pk'),
                    topic__state='published',
                    topic__archived_at__isnull=True,
                    state=Assignment.State.PUBLISHED,
                )
                .select_related('topic')
                .prefetch_related('criteria__levels')
            )
        if user.role == User.Role.TEACHER:
            courses = teacher_course_scope(user, active_only=True)
            return Assignment.objects.filter(
                topic__course_id__in=courses.values('pk'),
                topic__archived_at__isnull=True,
                state=Assignment.State.DRAFT,
            ).select_related('topic')
        return Assignment.objects.none()


@extend_schema_view(
    get=extend_schema(
        summary='Joriy o‘quvchining draftini o‘qish',
        description=(
            'Faqat joriy o‘quvchining faol enrollment’i bor published '
            'assignment draftini qaytaradi. Hali draft yozilmagan bo‘lsa '
            'bo‘sh matn, attachment_ids=[] va draft_version=0 qaytadi; GET '
            'konteyner yaratmaydi. Pupil faqat o‘z clean draft faylini ko‘radi.'
        ),
        responses={
            200: MyDraftReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    ),
    put=extend_schema(
        summary='Joriy o‘quvchining draftini saqlash',
        description=(
            'draft_version joriy qiymatga teng bo‘lishi shart. Muvaffaqiyatli '
            'saqlash versionni bittaga oshiradi; eskirgan version 409 qaytaradi. '
            'Pupil ID clientdan olinmaydi. attachment_ids berilsa 0–3 ta o‘ziga '
            'tegishli clean fayl bilan bog‘lanishni to‘liq almashtiradi; maydon '
            'tushib qolsa bog‘lanish saqlanadi.'
        ),
        request=MyDraftUpdateSerializer,
        responses={
            200: MyDraftReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    ),
)
class AssignmentMyDraftView(generics.GenericAPIView):
    serializer_class = MyDraftUpdateSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsPupil,
    )

    def get_queryset(self) -> QuerySet[Assignment]:
        courses = pupil_course_scope(self.request.user, active_only=True)
        return Assignment.objects.filter(
            topic__course_id__in=courses.values('pk'),
            topic__state='published',
            topic__archived_at__isnull=True,
            state=Assignment.State.PUBLISHED,
        )

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return MyDraftReadSerializer
        return MyDraftUpdateSerializer

    def get(self, request, *args, **kwargs):
        assignment = self.get_object()
        submission = assignment.submissions.filter(
            pupil=request.user,
        ).first()
        data = {
            'submission_id': submission.pk if submission else None,
            'assignment': assignment.pk,
            'draft_text': submission.draft_text if submission else '',
            'draft_version': submission.draft_version if submission else 0,
            'attachment_ids': (
                list(
                    submission.attachments.order_by('pk')
                    .values_list('pk', flat=True)
                )
                if submission else []
            ),
        }
        return Response(self.get_serializer(data).data)

    @transaction.atomic
    def put(self, request, *args, **kwargs):
        assignment = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = save_submission_draft(
            assignment.pk,
            request.user,
            draft_text=serializer.validated_data['draft_text'],
            expected_version=serializer.validated_data['draft_version'],
            attachment_ids=serializer.validated_data.get('attachment_ids'),
        )
        return Response(
            MyDraftReadSerializer({
                'submission_id': submission.pk,
                'assignment': assignment.pk,
                'draft_text': submission.draft_text,
                'draft_version': submission.draft_version,
                'attachment_ids': list(
                    submission.attachments.order_by('pk')
                    .values_list('pk', flat=True)
                ),
            }).data,
            status=status.HTTP_200_OK,
        )


class AssignmentSubmitView(generics.GenericAPIView):
    serializer_class = SubmitWorkSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsPupil,
    )

    def get_queryset(self) -> QuerySet[Assignment]:
        courses = pupil_course_scope(self.request.user, active_only=True)
        return Assignment.objects.filter(
            topic__course_id__in=courses.values('pk'),
            topic__state='published',
            topic__archived_at__isnull=True,
            state=Assignment.State.PUBLISHED,
        )

    @extend_schema(
        summary='Vazifa javobini yakuniy yuborish',
        description=(
            'Joriy draft_version majburiy. Server published assignment, '
            'faol enrollment, available_at/closes_at, max_attempts va bo‘sh '
            'ish qoidalarini tekshiradi. Draft matni hamda clean fayllar '
            'transaction’da immutable revisionga ko‘chadi. Idempotency-Key '
            'shu actor va assignment uchun majburiy; bir xil key+input oldingi '
            'receiptni qaytaradi, boshqa input 409 qaytaradi.'
        ),
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description='Bir submit urinishini qayta yuborishda o‘zgarmaydigan kalit.',
            ),
        ],
        request=SubmitWorkSerializer,
        responses={
            201: SubmitReceiptSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        assignment = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        idempotency_key = request.headers.get('Idempotency-Key', '')
        if (
            not idempotency_key
            or idempotency_key != idempotency_key.strip()
            or len(idempotency_key) > 255
        ):
            raise ValidationError({
                'Idempotency-Key': (
                    'Bo‘sh bo‘lmagan, chetida bo‘shliq yo‘q va 255 belgidan '
                    'oshmaydigan qiymat yuboring.'
                )
            })
        receipt_payload = submit_work(
            assignment.pk,
            request.user,
            expected_version=serializer.validated_data['draft_version'],
            idempotency_key=idempotency_key,
        )
        return Response(receipt_payload, status=status.HTTP_201_CREATED)


class SubmissionReturnView(generics.GenericAPIView):
    serializer_class = ReturnWorkSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self):
        courses = teacher_course_scope(
            self.request.user,
            active_only=True,
        )
        return (
            Submission.objects.filter(
                assignment__topic__course_id__in=courses.values('pk'),
                assignment__state=Assignment.State.PUBLISHED,
                assignment__topic__state='published',
                assignment__topic__archived_at__isnull=True,
                revisions__isnull=False,
            )
            .distinct()
        )

    @extend_schema(
        summary='Yuborilgan ishni o‘quvchiga qaytarish',
        description=(
            'Faqat faol, baholash huquqli assigned teacher o‘z kursidagi '
            'published assignment submissionini sabab bilan qaytaradi. '
            'Revision saqlanadi; pupil uchun yangi draft version ochiladi. '
            'Return deadline yoki max_attempts qiymatini uzaytirmaydi.'
        ),
        request=ReturnWorkSerializer,
        responses={
            201: ReturnReceiptSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        submission = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receipt = return_work(
            submission.pk,
            request.user,
            reason=serializer.validated_data['reason'],
        )
        return Response(receipt, status=status.HTTP_201_CREATED)


class RevisionGradeDraftView(generics.GenericAPIView):
    def get_permissions(self):
        role_permission = (
            IsPupil
            if self.request.method in ('GET', 'HEAD')
            else IsTeacher
        )
        return [
            IsAuthenticated(),
            MustChangePasswordPermission(),
            role_permission(),
        ]

    def get_queryset(self):
        if self.request.method == 'GET':
            courses = pupil_course_scope(
                self.request.user,
                active_only=True,
            )
            return (
                SubmissionRevision.objects.filter(
                    submission__pupil=self.request.user,
                    submission__assignment__topic__course_id__in=(
                        courses.values('pk')
                    ),
                    submission__assignment__state=Assignment.State.PUBLISHED,
                    submission__assignment__topic__state='published',
                    submission__assignment__topic__archived_at__isnull=True,
                    grade__state=Grade.State.PUBLISHED,
                )
                .select_related('grade')
                .prefetch_related('grade__criterion_scores')
            )
        courses = teacher_course_scope(
            self.request.user,
            active_only=True,
            can_grade=True,
        )
        return (
            SubmissionRevision.objects.filter(
                submission__assignment__topic__course_id__in=(
                    courses.values('pk')
                ),
                submission__assignment__state=Assignment.State.PUBLISHED,
                submission__assignment__topic__state='published',
                submission__assignment__topic__archived_at__isnull=True,
            )
            .select_related('submission__assignment')
        )

    @extend_schema(
        summary='O‘z revisionining e’lon qilingan bahosini ko‘rish',
        description=(
            'Pupil faqat o‘zining faol enrollment doirasidagi published '
            'revision bahosini ko‘radi. Draft yoki boshqa pupil bahosi 404.'
        ),
        responses={
            200: PublishedGradeReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    def get(self, request, *args, **kwargs):
        revision = self.get_object()
        return Response(PublishedGradeReadSerializer(revision.grade).data)

    @extend_schema(
        summary='Submitted revisionga draft baho yozish',
        description=(
            'Faqat faol can_grade teacher o‘z kursidagi submitted revisionga '
            'baho yozadi. Barcha published rubric criterion’lari uchun aynan '
            'bitta ball yuboriladi; ball limitlari tekshiriladi va total serverda '
            'hisoblanadi. version stale draft grade yozuvini aniqlaydi; pupil '
            'draft bahoni ko‘rmaydi.'
        ),
        request=GradeDraftWriteSerializer,
        responses={
            200: GradeDraftReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def put(self, request, *args, **kwargs):
        revision = self.get_object()
        criteria = list(
            Criterion.objects.filter(
                assignment=revision.submission.assignment,
            ).order_by('pk')
        )
        serializer = GradeDraftWriteSerializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                'criteria': criteria,
            },
        )
        serializer.is_valid(raise_exception=True)
        grade = save_grade_draft(
            revision.pk,
            request.user,
            expected_version=serializer.validated_data['version'],
            feedback=serializer.validated_data['feedback'],
            criterion_scores=serializer.validated_data['criterion_scores'],
        )
        return Response(
            GradeDraftReadSerializer(grade).data,
            status=status.HTTP_200_OK,
        )


class GradePublishView(generics.GenericAPIView):
    serializer_class = GradePublishSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self):
        courses = teacher_course_scope(
            self.request.user,
            active_only=True,
            can_grade=True,
        )
        return SubmissionRevision.objects.filter(
            submission__assignment__topic__course_id__in=(
                courses.values('pk')
            ),
            submission__assignment__state=Assignment.State.PUBLISHED,
            submission__assignment__topic__state='published',
            submission__assignment__topic__archived_at__isnull=True,
            grade__isnull=False,
        )

    @extend_schema(
        summary='Draft bahoni pupilga e’lon qilish',
        description=(
            'Grade va submitted revision versiyalarini tekshiradi, faol '
            'can_grade teacher hamda to‘liq/limitga mos rubric ballarini '
            'qayta validatsiya qiladi. State, published_at va audit yozuvi '
            'bitta transaction’da saqlanadi; takror publish natijani '
            'o‘zgartirmaydi.'
        ),
        request=GradePublishSerializer,
        responses={
            200: PublishedGradeReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        revision = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grade = publish_grade(
            revision.pk,
            request.user,
            expected_grade_version=serializer.validated_data[
                'grade_version'
            ],
            expected_revision_version=serializer.validated_data[
                'revision_version'
            ],
        )
        return Response(
            PublishedGradeReadSerializer(grade).data,
            status=status.HTTP_200_OK,
        )


class GradeRegradeView(generics.GenericAPIView):
    serializer_class = GradeRegradeSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self):
        courses = teacher_course_scope(
            self.request.user,
            active_only=True,
            can_grade=True,
        )
        return SubmissionRevision.objects.filter(
            submission__assignment__topic__course_id__in=(
                courses.values('pk')
            ),
            submission__assignment__state=Assignment.State.PUBLISHED,
            submission__assignment__topic__state='published',
            submission__assignment__topic__archived_at__isnull=True,
            grade__state=Grade.State.PUBLISHED,
        )

    @extend_schema(
        summary='Published bahoni sabab bilan qayta baholash',
        description=(
            'Faqat faol can_grade teacher joriy grade versionini yuboradi. '
            'Yangi rubric ballari serverda qayta hisoblanadi; yangi baho shu '
            'transaction’da darhol published bo‘ladi. Eski/yangi to‘liq snapshot, '
            'o‘zgartirgan teacher, sabab va vaqt GradeRevision auditida qoladi.'
        ),
        request=GradeRegradeSerializer,
        responses={
            200: PublishedGradeReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        revision = self.get_object()
        criteria = list(
            Criterion.objects.filter(
                assignment=revision.submission.assignment,
            ).order_by('pk')
        )
        serializer = self.get_serializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                'criteria': criteria,
            },
        )
        serializer.is_valid(raise_exception=True)
        grade = regrade_submission(
            revision.pk,
            request.user,
            expected_version=serializer.validated_data['expected_version'],
            criterion_scores=serializer.validated_data['criterion_scores'],
            reason=serializer.validated_data['reason'],
        )
        return Response(
            PublishedGradeReadSerializer(grade).data,
            status=status.HTTP_200_OK,
        )


class PupilGradebookView(generics.ListAPIView):
    serializer_class = PupilGradebookEntrySerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsPupil,
    )

    def get_queryset(self):
        return pupil_gradebook_queryset(
            self.request.user,
            self.request.query_params,
        )

    @extend_schema(
        summary='Pupilning shaxsiy baholar jurnali',
        description=(
            'Faqat request.user pupilning faol enrollmentidagi published '
            'assignmentlarni ko‘rsatadi. Eng yuqori revision raqamiga ega '
            'published baho current_grade; oldingi published revision baholari '
            'history bo‘ladi. Status: missing, ungraded, returned yoki graded. '
            'Bu endpoint chorak yakuniy bahosini hisoblamaydi.'
        ),
        parameters=[
            OpenApiParameter(
                name='course',
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name='term',
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={
            200: PupilGradebookEntrySerializer(many=True),
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CourseTeacherGradebookView(generics.ListAPIView):
    serializer_class = TeacherSubmissionQueueSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self):
        course = get_object_or_404(
            teacher_course_scope_queryset(self.request.user),
            pk=self.kwargs['pk'],
        )
        return teacher_submission_queue_queryset(course_id=course.pk)

    @extend_schema(
        summary='Faol biriktirilgan kursning topshirish/baho jurnali',
        description=(
            'Faqat faol biriktirilgan teacher kursining published assignment '
            'submissionlarini ko‘rsatadi. Har pupil uchun minimal ID/ism va '
            'eng so‘nggi revision hamda grade holati qaytadi; draft matn va '
            'fayl ma’lumotlari berilmaydi.'
        ),
        responses={
            200: TeacherSubmissionQueueSerializer(many=True),
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AssignmentSubmissionQueueView(generics.ListAPIView):
    serializer_class = TeacherSubmissionQueueSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self):
        courses = teacher_course_scope_queryset(self.request.user)
        assignment = get_object_or_404(
            Assignment.objects.filter(
                topic__course_id__in=courses.values('pk'),
                topic__state='published',
                topic__archived_at__isnull=True,
                state=Assignment.State.PUBLISHED,
            ),
            pk=self.kwargs['pk'],
        )
        return teacher_submission_queue_queryset(
            assignment_id=assignment.pk,
        )

    @extend_schema(
        summary='Vazifa bo‘yicha submitted/graded tekshirish navbati',
        description=(
            'Faol assigned teacher faqat o‘z published vazifasidagi submitted '
            'ishlarni ko‘radi. Eng so‘nggi revision statusi submitted, draft, '
            'graded yoki returned; pupil identifikatori minimal.'
        ),
        responses={
            200: TeacherSubmissionQueueSerializer(many=True),
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AssignmentRubricView(generics.GenericAPIView):
    serializer_class = RubricReplacementSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )

    def get_queryset(self) -> QuerySet[Assignment]:
        user = self.request.user
        if user.role != User.Role.TEACHER:
            return Assignment.objects.none()
        courses = teacher_course_scope(user, active_only=True)
        return Assignment.objects.filter(
            topic__course_id__in=courses.values('pk'),
            topic__archived_at__isnull=True,
            state=Assignment.State.DRAFT,
        ).select_for_update()

    @extend_schema(
        summary='Draft vazifa rubricini to‘liq almashtirish',
        description=(
            'Yuborilgan criteria va levels butun rubricning yangi holati '
            'hisoblanadi. Mavjud child ID’lari faqat shu assignmentga tegishli '
            'bo‘lishi kerak; yuborilmagan child qatorlari o‘chiriladi.'
        ),
        request=RubricReplacementSerializer,
        responses={
            200: RubricReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def put(self, request, *args, **kwargs):
        assignment = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            context={
                **self.get_serializer_context(),
                'assignment': assignment,
            },
        )
        serializer.is_valid(raise_exception=True)
        assignment = replace_assignment_rubric(
            assignment.pk,
            serializer.validated_data['criteria'],
        )
        assignment = (
            Assignment.objects.prefetch_related('criteria__levels')
            .get(pk=assignment.pk)
        )
        return Response(
            RubricReadSerializer(assignment).data,
            status=status.HTTP_200_OK,
        )


class AssignmentPublishView(generics.GenericAPIView):
    serializer_class = AssignmentReadSerializer
    permission_classes = (
        IsAuthenticated,
        MustChangePasswordPermission,
        IsTeacher,
    )
    queryset = Assignment.objects.none()

    def get_queryset(self) -> QuerySet[Assignment]:
        user = self.request.user
        if user.role != User.Role.TEACHER:
            return Assignment.objects.none()
        courses = teacher_course_scope(user, active_only=True)
        return Assignment.objects.filter(
            topic__course_id__in=courses.values('pk'),
            topic__archived_at__isnull=True,
        )

    @extend_schema(
        summary='Draft vazifani nashr qilish',
        description=(
            'Rubric yig‘indisi max_scorega teng, har bir criterionda 0 ballli '
            'level bor va vazifa maydonlari to‘liq bo‘lgandagina publish qiladi. '
            'Qayta POST idempotent; notification bu endpointda yuborilmaydi.'
        ),
        request=None,
        responses={
            200: AssignmentReadSerializer,
            **common_error_responses(),
        },
        tags=['Assignments'],
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        self.get_object()
        assignment = publish_assignment(self.kwargs['pk'])
        return Response(
            AssignmentReadSerializer(assignment).data,
            status=status.HTTP_200_OK,
        )
