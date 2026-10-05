from django.conf import settings
from django.db import models


class NotificationKind(models.TextChoices):
    DUE_SOON = "due_soon", "Due soon"
    OVERDUE = "overdue", "Overdue"
    RESERVATION_READY = "reservation_ready", "Reservation available"
    RESERVATION_EXPIRING = "reservation_expiring", "Reservation expiring"
    RESERVATION_EXPIRED = "reservation_expired", "Reservation expired"
    FEE = "fee", "Fee generated"
    PAYMENT = "payment", "Payment received"
    ANNOUNCEMENT = "announcement", "Announcement"
    ACCOUNT = "account", "Account"


class Notification(models.Model):
    """In-app notification shown in the member's notification centre."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=NotificationKind.choices)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=160, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return self.title


class EmailStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class EmailLog(models.Model):
    """Every email the system sends, with its rendered content for retries
    and for staff to answer 'did they get the reminder?'."""

    to_email = models.EmailField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="emails"
    )
    template = models.CharField(max_length=60, db_index=True)
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=EmailStatus.choices, default=EmailStatus.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=160, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.template} → {self.to_email} ({self.status})"


class Announcement(models.Model):
    title = models.CharField(max_length=160)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    recipients = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
