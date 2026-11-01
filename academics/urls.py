from django.urls import path

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
    AvailableSubjectOptionListView,
    CourseDetailView,
    CourseListView,
    CourseOfferingCreateView,
    CourseOfferingUpdateView,
    SchoolClassOptionListView,
)

urlpatterns = [
    path("auth/csrf/", CsrfCookieView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
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
    path("courses/<int:pk>/", CourseDetailView.as_view(), name="course-detail"),
    path("admin/courses/", CourseOfferingCreateView.as_view(), name="course-create"),
    path(
        "admin/courses/<int:pk>/",
        CourseOfferingUpdateView.as_view(),
        name="course-update",
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
