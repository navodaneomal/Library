from django.conf import settings
from django.db import models


class InventorySession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "In progress"
        COMPLETED = "completed", "Completed"

    name = models.CharField(max_length=120)
    scope_prefix = models.CharField(
        "Shelves",
        max_length=40,
        blank=True,
        help_text="Optional — e.g. FIC-A to check only shelves starting with FIC-A.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return self.name


class ScanResult(models.TextChoices):
    OK = "ok", "Scanned"
    UNEXPECTED_LOCATION = "unexpected_location", "Unexpected location"
    DUPLICATE = "duplicate", "Duplicate scan"
    UNKNOWN = "unknown", "Not registered"
    STATUS_MISMATCH = "status_mismatch", "Needs attention"


class InventoryScan(models.Model):
    session = models.ForeignKey(InventorySession, on_delete=models.CASCADE, related_name="scans")
    barcode = models.CharField(max_length=64)
    copy = models.ForeignKey("catalogue.BookCopy", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    location = models.CharField(max_length=40, blank=True)
    result = models.CharField(max_length=30, choices=ScanResult.choices)
    note = models.CharField(max_length=255, blank=True)
    scanned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scanned_at", "-id"]
        indexes = [models.Index(fields=["session", "copy"])]

    def __str__(self):
        return f"{self.barcode}: {self.get_result_display()}"
