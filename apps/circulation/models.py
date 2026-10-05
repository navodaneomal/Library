from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class LoanStatus(models.TextChoices):
    ACTIVE = "active", "On loan"
    RETURNED = "returned", "Returned"
    LOST = "lost", "Lost"


class LoanQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=LoanStatus.ACTIVE)

    def overdue(self, today=None):
        today = today or timezone.localdate()
        return self.active().filter(due_date__lt=today)


class Loan(models.Model):
    """A borrowing of one physical copy. The return is recorded on the same
    row (returned_at / returned_by) — a separate 'returns' table would only
    duplicate it."""

    copy = models.ForeignKey("catalogue.BookCopy", on_delete=models.PROTECT, related_name="loans")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="loans")
    borrowed_at = models.DateTimeField(default=timezone.now, db_index=True)
    due_date = models.DateField(db_index=True)
    returned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=LoanStatus.choices, default=LoanStatus.ACTIVE, db_index=True)
    renew_count = models.PositiveSmallIntegerField(default=0)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = LoanQuerySet.as_manager()

    class Meta:
        ordering = ["-borrowed_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["copy"], condition=Q(status="active"), name="one_active_loan_per_copy"),
        ]
        indexes = [
            models.Index(fields=["member", "status"]),
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self):
        return f"{self.copy.book.title} → {self.member.member_id}"

    @property
    def book(self):
        return self.copy.book

    @property
    def is_active(self) -> bool:
        return self.status == LoanStatus.ACTIVE

    @property
    def borrowed_on(self):
        return timezone.localdate(self.borrowed_at)

    @property
    def returned_on(self):
        return timezone.localdate(self.returned_at) if self.returned_at else None

    def days_overdue(self, on=None) -> int:
        end = on or self.returned_on or timezone.localdate()
        return max(0, (end - self.due_date).days)

    def is_overdue(self, today=None) -> bool:
        return self.is_active and self.due_date < (today or timezone.localdate())

    def fee_quote(self, on=None):
        from apps.fees.engine import quote_for_loan

        return quote_for_loan(self, on=on)


class ReservationStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    READY = "ready", "Ready for collection"
    COLLECTED = "collected", "Collected"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


OPEN_RESERVATION_STATUSES = (ReservationStatus.WAITING, ReservationStatus.READY)


class Reservation(models.Model):
    """A member's place in a title's first-come, first-served queue."""

    book = models.ForeignKey("catalogue.Book", on_delete=models.PROTECT, related_name="reservations")
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(
        max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.WAITING, db_index=True
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    copy = models.ForeignKey(
        "catalogue.BookCopy", null=True, blank=True, on_delete=models.SET_NULL, related_name="holds"
    )
    ready_at = models.DateTimeField(null=True, blank=True)
    picked_at = models.DateTimeField(null=True, blank=True, help_text="When staff placed the copy on the hold shelf.")
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    loan = models.ForeignKey(Loan, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    expiry_notified = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "member"],
                condition=Q(status__in=["waiting", "ready"]),
                name="one_open_reservation_per_member_and_book",
            ),
            models.UniqueConstraint(fields=["copy"], condition=Q(status="ready"), name="one_ready_hold_per_copy"),
        ]
        indexes = [models.Index(fields=["book", "status", "created_at"])]

    def __str__(self):
        return f"{self.book.title} for {self.member.member_id} ({self.get_status_display()})"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_RESERVATION_STATUSES

    @property
    def collect_by(self):
        return (
            timezone.localdate(self.expires_at) if self.expires_at and self.status == ReservationStatus.READY else None
        )
