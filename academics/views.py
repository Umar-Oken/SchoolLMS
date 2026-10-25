from django.db.models import Prefetch
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from accounts.models import User
from academics.models import (
    CourseOffering,
    MembershipStatus,
    TeachingAssignment,
)
from academics.serializers import CourseReadSerializer


class CourseReadQuerysetMixin:
    serializer_class = CourseReadSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        queryset = (
            CourseOffering.objects.filter(archived_at__isnull=True)
            .select_related(
                "subject",
                "school_class",
                "school_class__year",
            )
            .prefetch_related(
                Prefetch(
                    "teaching_assignments",
                    queryset=TeachingAssignment.objects.filter(
                        end_date__isnull=True
                    )
                    .select_related("teacher")
                    .order_by("start_date", "pk"),
                    to_attr="active_assignments",
                )
            )
        )

        if user.role == User.Role.PUPIL:
            queryset = queryset.filter(
                pupil_enrollments__pupil=user,
                pupil_enrollments__status=MembershipStatus.ACTIVE,
                pupil_enrollments__end_date__isnull=True,
            )
        elif user.role == User.Role.TEACHER:
            queryset = queryset.filter(
                teaching_assignments__teacher=user,
                teaching_assignments__end_date__isnull=True,
            )
        else:
            return queryset.none()

        return queryset.distinct()


class CourseListView(CourseReadQuerysetMixin, ListAPIView):
    pass


class CourseDetailView(CourseReadQuerysetMixin, RetrieveAPIView):
    pass
