from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from accounts.models import User
from assignments.models import Grade, Submission, SubmissionRevision


class MinimalPupilSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'first_name', 'last_name')
        read_only_fields = fields


class TeacherGradeSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Grade
        fields = ('state', 'total', 'version', 'published_at')
        read_only_fields = fields


class TeacherRevisionQueueSerializer(serializers.ModelSerializer):
    grade = TeacherGradeSummarySerializer(read_only=True, allow_null=True)

    class Meta:
        model = SubmissionRevision
        fields = (
            'id',
            'revision_number',
            'submitted_at',
            'is_late',
            'grade',
        )
        read_only_fields = fields


class TeacherSubmissionQueueSerializer(serializers.ModelSerializer):
    pupil = MinimalPupilSerializer(read_only=True)
    assignment_id = serializers.IntegerField(read_only=True)
    assignment_title = serializers.CharField(
        source='assignment.title',
        read_only=True,
    )
    latest_revision = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = (
            'id',
            'pupil',
            'assignment_id',
            'assignment_title',
            'status',
            'latest_revision',
        )
        read_only_fields = fields

    def _latest_revision(self, submission):
        revisions = getattr(submission, 'queue_revisions', ())
        return revisions[-1] if revisions else None

    @extend_schema_field(TeacherRevisionQueueSerializer(allow_null=True))
    def get_latest_revision(self, submission):
        revision = self._latest_revision(submission)
        if revision is None:
            return None
        return TeacherRevisionQueueSerializer(revision).data

    @extend_schema_field(OpenApiTypes.STR)
    def get_status(self, submission):
        revision = self._latest_revision(submission)
        if revision is None:
            return 'missing'
        if hasattr(revision, 'return_record'):
            return 'returned'
        if not hasattr(revision, 'grade'):
            return 'submitted'
        if revision.grade.state == Grade.State.DRAFT:
            return 'draft'
        return 'graded'
