from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import (
    AccountCreateView,
    AccountResetPasswordView,
    CsrfCookieView,
    ChangePasswordView,
    CurrentUserView,
    LoginView,
    LogoutView,
)
from academics.views import (
    AcademicYearAggregateCreateView,
    AcademicYearAggregateUpdateView,
    AcademicYearCatalogDetailView,
    AcademicYearCatalogListView,
    AvailableSubjectOptionListView,
    ClassMembershipCreateView,
    CourseDetailView,
    CourseEnrollmentSyncView,
    CourseListView,
    CourseOfferingCreateView,
    CourseOfferingUpdateView,
    CoursePupilRosterView,
    SchoolClassCatalogListCreateView,
    SchoolClassCatalogRetrieveUpdateView,
    SchoolClassOptionListView,
    SubjectAdminListCreateView,
    SubjectAdminRetrieveUpdateView,
    SubjectArchiveView,
    SubjectCatalogListView,
    TeacherOptionListView,
    TeachingAssignmentCloseView,
    TeachingAssignmentCreateView,
)

urlpatterns = [
    path("auth/csrf/", CsrfCookieView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path(
        "auth/token/refresh/",
        TokenRefreshView.as_view(),
        name="auth-token-refresh",
    ),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path(
        "me/change-password/",
        ChangePasswordView.as_view(),
        name="change-password",
    ),
    path("accounts/", AccountCreateView.as_view(), name="account-create"),
    path(
        "accounts/<int:pk>/reset-password/",
        AccountResetPasswordView.as_view(),
        name="account-reset-password",
    ),
    path("courses/", CourseListView.as_view(), name="course-list"),
    path(
        "courses/<int:course_pk>/pupils/",
        CoursePupilRosterView.as_view(),
        name="course-pupils",
    ),
    path("courses/<int:pk>/", CourseDetailView.as_view(), name="course-detail"),
    path("admin/courses/", CourseOfferingCreateView.as_view(), name="course-create"),
    path(
        "admin/courses/<int:course_pk>/sync-enrollments/",
        CourseEnrollmentSyncView.as_view(),
        name="course-enrollment-sync",
    ),
    path(
        "admin/courses/<int:pk>/",
        CourseOfferingUpdateView.as_view(),
        name="course-update",
    ),
    path(
        "catalog/academic-years/",
        AcademicYearCatalogListView.as_view(),
        name="academic-year-catalog-list",
    ),
    path(
        "catalog/academic-years/<int:pk>/",
        AcademicYearCatalogDetailView.as_view(),
        name="academic-year-catalog-detail",
    ),
    path(
        "admin/academic-years/",
        AcademicYearAggregateCreateView.as_view(),
        name="academic-year-create",
    ),
    path(
        "admin/academic-years/<int:pk>/",
        AcademicYearAggregateUpdateView.as_view(),
        name="academic-year-update",
    ),
    path(
        "admin/classes/",
        SchoolClassCatalogListCreateView.as_view(),
        name="school-class-list-create",
    ),
    path(
        "admin/classes/<int:pk>/",
        SchoolClassCatalogRetrieveUpdateView.as_view(),
        name="school-class-detail-update",
    ),
    path(
        "catalog/subjects/",
        SubjectCatalogListView.as_view(),
        name="subject-catalog-list",
    ),
    path(
        "admin/subjects/",
        SubjectAdminListCreateView.as_view(),
        name="subject-admin-list-create",
    ),
    path(
        "admin/subjects/<int:pk>/",
        SubjectAdminRetrieveUpdateView.as_view(),
        name="subject-admin-detail-update",
    ),
    path(
        "admin/subjects/<int:pk>/archive/",
        SubjectArchiveView.as_view(),
        name="subject-archive",
    ),
    path(
        "catalog/teachers/",
        TeacherOptionListView.as_view(),
        name="teacher-options",
    ),
    path(
        "admin/teaching-assignments/",
        TeachingAssignmentCreateView.as_view(),
        name="teaching-assignment-create",
    ),
    path(
        "admin/teaching-assignments/<int:pk>/close/",
        TeachingAssignmentCloseView.as_view(),
        name="teaching-assignment-close",
    ),
    path(
        "admin/class-memberships/",
        ClassMembershipCreateView.as_view(),
        name="class-membership-create",
    ),
    path(
        "catalog/classes/",
        SchoolClassOptionListView.as_view(),
        name="catalog-class-options",
    ),
    path(
        "catalog/classes/<int:school_class_id>/subjects/",
        AvailableSubjectOptionListView.as_view(),
        name="catalog-subject-options",
    ),
]
