from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from assignments.models import Grade


class DashboardLessonSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='pk')
    course_id = serializers.IntegerField(source='slot.course_id')
    course = serializers.CharField(source='slot.course.subject.title')
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    room = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_room(self, session):
        room = session.room_override or session.slot.room
        return room.name


class DashboardDeadlineSerializer(serializers.Serializer):
    assignment_id = serializers.IntegerField(source='pk')
    topic_id = serializers.IntegerField()
    course_id = serializers.IntegerField(source='topic.course_id')
    title = serializers.CharField()
    due_at = serializers.DateTimeField()
    completion_status = serializers.SerializerMethodField()
    submission_status = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=('completed', 'not_completed')))
    def get_completion_status(self, assignment):
        return (
            'completed'
            if assignment.topic_id in self.context['completed_topic_ids']
            else 'not_completed'
        )

    @extend_schema_field(serializers.ChoiceField(
        choices=('missing', 'returned', 'submitted_ungraded', 'graded'),
    ))
    def get_submission_status(self, assignment):
        submissions = getattr(assignment, 'pupil_submissions', ())
        if not submissions:
            return 'missing'
        revisions = submissions[0].dashboard_revisions
        if not revisions:
            return 'missing'
        latest = revisions[-1]
        if hasattr(latest, 'return_record'):
            return 'returned'
        if not (hasattr(latest, 'grade') and latest.grade.state == Grade.State.PUBLISHED):
            return 'submitted_ungraded'
        return 'graded'


class DashboardFeedbackSerializer(serializers.Serializer):
    assignment_id = serializers.IntegerField(source='revision.submission.assignment_id')
    title = serializers.CharField(source='revision.submission.assignment.title')
    course_id = serializers.IntegerField(source='revision.submission.assignment.topic.course_id')
    total = serializers.DecimalField(max_digits=8, decimal_places=2)
    feedback = serializers.CharField()
    published_at = serializers.DateTimeField()


class DashboardCompletionSerializer(serializers.Serializer):
    eligible_topics = serializers.IntegerField(min_value=0)
    completed_topics = serializers.IntegerField(min_value=0)


class PupilDashboardSerializer(serializers.Serializer):
    today_lessons = DashboardLessonSerializer(many=True)
    completion = DashboardCompletionSerializer()
    upcoming_deadlines = DashboardDeadlineSerializer(many=True)
    recent_feedback = DashboardFeedbackSerializer(many=True)


class TeacherDashboardSessionSerializer(serializers.Serializer):
    id = serializers.IntegerField(source='pk')
    course_id = serializers.IntegerField(source='slot.course_id')
    course = serializers.CharField(source='slot.course.subject.title')
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    room = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_room(self, session):
        room = session.room_override or session.slot.room
        return room.name


class TeacherQueueLinkSerializer(serializers.Serializer):
    endpoint_template = serializers.CharField()
    query = serializers.DictField(child=serializers.CharField())
    description = serializers.CharField()


class TeacherReviewSummarySerializer(serializers.Serializer):
    submitted_ungraded_count = serializers.IntegerField(min_value=0)
    returned_count = serializers.IntegerField(min_value=0)
    late_submitted_ungraded_count = serializers.IntegerField(min_value=0)
    late_queue = TeacherQueueLinkSerializer()


class TeacherDashboardSerializer(serializers.Serializer):
    today_sessions = TeacherDashboardSessionSerializer(many=True)
    review_summary = TeacherReviewSummarySerializer()
