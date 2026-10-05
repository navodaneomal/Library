from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

MONEY = {"max_digits": 10, "decimal_places": 2}


class LibrarySettings(models.Model):
    """Everything an administrator can configure, stored in a single row (pk=1).

    Nothing here is a secret: SMTP credentials, database URLs and keys live in
    environment variables, never in the database.
    """

    # Library
    library_name = models.CharField(max_length=120, default="City Library")
    library_email = models.EmailField(
        "Library email", blank=True, help_text="Shown to members and used as the reply-to address."
    )
    library_phone = models.CharField(max_length=40, blank=True)
    library_address = models.CharField(max_length=255, blank=True)
    opening_hours = models.TextField(
        blank=True,
        default="Monday – Friday: 8:30 – 18:00\nSaturday: 9:00 – 13:00\nSunday & public holidays: Closed",
    )
    currency = models.CharField(
        max_length=3,
        default="LKR",
        validators=[RegexValidator(r"^[A-Z]{3}$", "Use a three-letter currency code such as LKR or USD.")],
    )

    # Loans
    loan_period_days = models.PositiveSmallIntegerField(
        "Loan duration (days)", default=14, validators=[MinValueValidator(1), MaxValueValidator(365)]
    )
    max_active_loans = models.PositiveSmallIntegerField(
        "Maximum books per member", default=5, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    max_renewals = models.PositiveSmallIntegerField(
        "Renewals allowed per loan", default=1, validators=[MaxValueValidator(10)], help_text="0 disables renewals."
    )
    renewal_days = models.PositiveSmallIntegerField(
        "Days added per renewal", default=7, validators=[MinValueValidator(1), MaxValueValidator(90)]
    )
    allow_duplicate_title_loans = models.BooleanField("Allow borrowing two copies of the same title", default=False)
    block_on_overdue = models.BooleanField("Block borrowing while books are overdue", default=True)
    fee_block_threshold = models.DecimalField(
        "Block borrowing when unpaid fees exceed",
        default=Decimal("500.00"),
        validators=[MinValueValidator(0)],
        help_text="Set to 0 to block borrowing whenever any fee is unpaid.",
        **MONEY,
    )

    # Fees
    grace_period_days = models.PositiveSmallIntegerField(
        "Grace period (days)",
        default=0,
        validators=[MaxValueValidator(60)],
        help_text="The first overdue days that are free of charge.",
    )
    late_fee_per_day = models.DecimalField(
        "Fee per overdue day", default=Decimal("50.00"), validators=[MinValueValidator(0)], **MONEY
    )
    max_late_fee = models.DecimalField(
        "Maximum late fee per loan",
        default=Decimal("1000.00"),
        validators=[MinValueValidator(0)],
        help_text="Set to 0 for no maximum.",
        **MONEY,
    )
    default_replacement_fee = models.DecimalField(
        "Default replacement fee for lost books",
        default=Decimal("2500.00"),
        validators=[MinValueValidator(0)],
        help_text="Used when a book has no replacement cost of its own.",
        **MONEY,
    )

    # Reservations
    reservation_hold_days = models.PositiveSmallIntegerField(
        "Days to collect a reserved book",
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="After this, the book passes to the next member in the queue.",
    )
    reservation_waiting_expiry_days = models.PositiveSmallIntegerField(
        "Waiting reservations expire after (days)",
        default=0,
        validators=[MaxValueValidator(365)],
        help_text="0 means waiting reservations never expire.",
    )
    max_reservations_per_member = models.PositiveSmallIntegerField(
        "Maximum active reservations per member", default=5, validators=[MinValueValidator(1), MaxValueValidator(50)]
    )

    # Reminders
    reminders_enabled = models.BooleanField("Send automatic email reminders", default=True)
    due_soon_reminder_days = models.PositiveSmallIntegerField(
        "Remind members this many days before the due date",
        default=3,
        validators=[MaxValueValidator(30)],
        help_text="0 disables due-soon reminders.",
    )
    overdue_reminder_interval_days = models.PositiveSmallIntegerField(
        "Repeat overdue reminders every (days)", default=7, validators=[MinValueValidator(1), MaxValueValidator(60)]
    )

    # Registration
    allow_self_registration = models.BooleanField("Members can register online", default=True)
    member_id_prefix = models.CharField(
        max_length=10,
        default="LIB",
        validators=[RegexValidator(r"^[A-Z0-9]{2,10}$", "Use 2–10 capital letters or digits.")],
        help_text="Member IDs look like LIB-2026-00482.",
    )
    require_phone = models.BooleanField("Phone number required at registration", default=True)
    require_address = models.BooleanField("Address required at registration", default=False)
    require_date_of_birth = models.BooleanField("Date of birth required at registration", default=False)
    require_email_verification = models.BooleanField("Members must verify their email before reserving", default=False)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "library settings"
        verbose_name_plural = "library settings"

    def __str__(self):
        return f"Settings for {self.library_name}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "LibrarySettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AuditLog(models.Model):
    """Accountability trail for important staff actions."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    summary = models.CharField(max_length=255)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    target_label = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self):
        return f"{self.actor_name or 'System'}: {self.summary}"
