from django.conf import settings
from django.db import models


class MemberStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    INACTIVE = "inactive", "Inactive"


class Member(models.Model):
    """Library-specific profile attached to a user account."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="member")
    member_id = models.CharField("Member ID", max_length=32, unique=True)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    address = models.CharField(max_length=255, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=MemberStatus.choices, default=MemberStatus.ACTIVE, db_index=True)
    status_reason = models.CharField(max_length=255, blank=True)
    email_reminders = models.BooleanField("Email me due-date reminders", default=True)
    staff_notes = models.TextField(blank=True, help_text="Only visible to staff.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["user__full_name"]

    def __str__(self):
        return f"{self.user.full_name} ({self.member_id})"

    @property
    def name(self) -> str:
        return self.user.full_name

    @property
    def email(self) -> str | None:
        return self.user.email

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE
