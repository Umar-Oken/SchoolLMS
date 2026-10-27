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
from academics.views import CourseDetailView, CourseListView

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
]
