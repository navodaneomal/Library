from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

MONEY = {"max_digits": 10, "decimal_places": 2}


class FeeKind(models.TextChoices):
    LATE = "late", "Late return"
    REPLACEMENT = "replacement", "Lost book replacement"
    DAMAGE = "damage", "Damage"
    OTHER = "other", "Other"


class FeeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    WAIVED = "waived", "Waived"


class Fee(models.Model):
    member = models.ForeignKey("members.Member", on_delete=models.PROTECT, related_name="fees")
    loan = models.ForeignKey("circulation.Loan", null=True, blank=True, on_delete=models.SET_NULL, related_name="fees")
    kind = models.CharField(max_length=20, choices=FeeKind.choices, default=FeeKind.LATE)
    amount = models.DecimalField(**MONEY)
    amount_paid = models.DecimalField(default=Decimal("0.00"), **MONEY)
    status = models.CharField(max_length=20, choices=FeeStatus.choices, default=FeeStatus.PENDING, db_index=True)
    days_overdue = models.PositiveIntegerField(null=True, blank=True)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    waive_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="fee_amount_not_negative"),
            models.CheckConstraint(condition=Q(amount_paid__gte=0), name="fee_paid_not_negative"),
        ]
        indexes = [models.Index(fields=["member", "status"])]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.amount} ({self.get_status_display()})"

    @property
    def outstanding(self) -> Decimal:
        if self.status != FeeStatus.PENDING:
            return Decimal("0.00")
        return max(Decimal("0.00"), self.amount - self.amount_paid)

    @property
    def book(self):
        return self.loan.copy.book if self.loan_id else None


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    CARD = "card", "Card"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    ONLINE = "online", "Online"
    OTHER = "other", "Other"


class Payment(models.Model):
    """A payment towards a fee. `provider` / `reference` let an online payment
    gateway record its own transactions later without schema changes."""

    fee = models.ForeignKey(Fee, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(**MONEY)
    paid_on = models.DateField(default=timezone.localdate)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField("Receipt / reference number", max_length=100, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.amount} via {self.get_method_display()}"
