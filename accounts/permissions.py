from rest_framework.permissions import BasePermission


class MustChangePasswordPermission(BasePermission):
    message = "Davom etishdan oldin parolingizni o‘zgartiring."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and not request.user.must_change_password
        )


class CanManageAccounts(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.has_perm(
            "accounts.manage_accounts"
        )


class CanManageAcademics(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.has_perm(
            "accounts.manage_academics"
        )
