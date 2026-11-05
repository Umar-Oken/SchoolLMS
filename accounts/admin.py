from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group

from accounts.models import AccountLifecycleAudit, CapabilityGrantAudit, User


@admin.register(User)
class LMSUserAdmin(UserAdmin):
    list_display = (
        "username",
        "first_name",
        "last_name",
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
    )
    list_filter = (
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
    )
    fieldsets = list(UserAdmin.fieldsets or ()) + [
        (
            "School LMS",
            {
                "fields": (
                    "role",
                    "preferred_language",
                    "must_change_password",
                )
            },
        ),
    ]
    add_fieldsets = list(UserAdmin.add_fieldsets or ()) + [
        (None, {"fields": ("role",)}),
    ]

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets

        restricted_fields = {"is_superuser", "groups", "user_permissions"}
        return tuple(
            (title, {**options, "fields": tuple(
                field
                for field in options.get("fields", ())
                if field not in restricted_fields
            )})
            for title, options in fieldsets
            if any(
                field not in restricted_fields
                for field in options.get("fields", ())
            )
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields.extend(("is_active", "is_staff"))
        return fields


admin.site.unregister(Group)


@admin.register(Group)
class SuperuserGroupAdmin(GroupAdmin):
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CapabilityGrantAudit)
class CapabilityGrantAuditAdmin(ReadOnlyAuditAdmin):
    list_display = ("target", "capability", "actor", "reason", "created_at")
    list_filter = ("capability", "created_at")
    search_fields = ("target__username", "actor__username", "reason")
    ordering = ("-created_at",)


@admin.register(AccountLifecycleAudit)
class AccountLifecycleAuditAdmin(ReadOnlyAuditAdmin):
    list_display = ("target", "action", "actor", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("target__username", "actor__username")
    ordering = ("-created_at",)
