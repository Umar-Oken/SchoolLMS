from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from assignments.models import Assignment, CriterionScore, Grade


class GradebookCriterionScoreSerializer(serializers.ModelSerializer):
    criterion_id = serializers.IntegerField(read_only=True)
    criterion_label = serializers.CharField(
        source='criterion.label',
        read_only=True,
    )

    class Meta:
        model = CriterionScore
        fields = ('criterion_id', 'criterion_label', 'points', 'feedback')
        read_only_fields = fields


class GradebookGradeSerializer(serializers.ModelSerializer):
    revision_id = serializers.IntegerField(read_only=True)
    revision_number = serializers.IntegerField(
        source='revision.revision_number',
        read_only=True,
    )
    criterion_scores = GradebookCriterionScoreSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = Grade
        fields = (
            'revision_id',
            'revision_number',
            'total',
            'feedback',
            'published_at',
            'criterion_scores',
        )
        read_only_fields = fields


class PupilGradebookEntrySerializer(serializers.ModelSerializer):
    assignment_id = serializers.IntegerField(source='pk', read_only=True)
    course_id = serializers.IntegerField(
        source='topic.course_id',
        read_only=True,
    )
    course = serializers.CharField(
        source='topic.course.subject.title',
        read_only=True,
    )
    term_id = serializers.IntegerField(source='topic.term_id', read_only=True)
    term = serializers.IntegerField(
        source='topic.term.order',
        read_only=True,
    )
    current_grade = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = (
            'assignment_id',
            'title',
            'course_id',
            'course',
            'term_id',
            'term',
            'due_at',
            'max_score',
            'status',
            'current_grade',
            'history',
        )
        read_only_fields = fields

    def _revisions(self, assignment):
        submissions = getattr(assignment, 'pupil_submissions', ())
        if not submissions:
            return []
        return submissions[0].gradebook_revisions

    def _published_grades(self, assignment):
        return [
            revision.grade
            for revision in self._revisions(assignment)
            if hasattr(revision, 'grade')
            and revision.grade.state == Grade.State.PUBLISHED
        ]

    @extend_schema_field(GradebookGradeSerializer(allow_null=True))
    def get_current_grade(self, assignment):
        grades = self._published_grades(assignment)
        if not grades:
            return None
        return GradebookGradeSerializer(
            max(grades, key=lambda grade: grade.revision.revision_number),
        ).data

    @extend_schema_field(GradebookGradeSerializer(many=True))
    def get_history(self, assignment):
        grades = self._published_grades(assignment)
        if len(grades) < 2:
            return []
        current = max(
            grades,
            key=lambda grade: grade.revision.revision_number,
        )
        previous = sorted(
            (grade for grade in grades if grade.pk != current.pk),
            key=lambda grade: grade.revision.revision_number,
            reverse=True,
        )
        return GradebookGradeSerializer(previous, many=True).data

    @extend_schema_field(OpenApiTypes.STR)
    def get_status(self, assignment):
        revisions = self._revisions(assignment)
        if not revisions:
            return 'missing'
        latest = revisions[-1]
        if hasattr(latest, 'return_record'):
            return 'returned'
        if not (
            hasattr(latest, 'grade')
            and latest.grade.state == Grade.State.PUBLISHED
        ):
            return 'ungraded'
        return 'graded'
