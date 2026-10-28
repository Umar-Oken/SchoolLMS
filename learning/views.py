from rest_framework import viewsets
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated

from accounts.models import User
from accounts.permissions import MustChangePasswordPermission
from academics.models import CourseOffering
from academics.views import pupil_course_scope, teacher_course_scope
from learning.models import Topic
from learning.serializers import TopicReadSerializer


class TopicReadViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TopicReadSerializer
    permission_classes = (IsAuthenticated, MustChangePasswordPermission)

    def _course_scope(self):
        user = self.request.user
        if not isinstance(user, User):
            return CourseOffering.objects.none()
        if user.role == User.Role.PUPIL:
            return pupil_course_scope(user)
        if user.role == User.Role.TEACHER:
            return teacher_course_scope(user)
        return CourseOffering.objects.none()

    def get_queryset(self):
        user = self.request.user
        if not isinstance(user, User):
            return Topic.objects.none()

        queryset = Topic.objects.select_related(
            'course',
            'term',
            'term__year',
            'course__school_class',
            'course__school_class__year',
        ).filter(course_id__in=self._course_scope().values('pk'))

        if user.role == User.Role.PUPIL:
            queryset = queryset.filter(state=Topic.State.PUBLISHED)

        course_pk = self.kwargs.get('course_pk')
        if course_pk is not None:
            queryset = queryset.filter(course_id=course_pk)

        return queryset

    def list(self, request, *args, **kwargs):
        course_pk = kwargs.get('course_pk')
        if (
            course_pk is not None
            and not self._course_scope().filter(pk=course_pk).exists()
        ):
            raise NotFound()
        return super().list(request, *args, **kwargs)
