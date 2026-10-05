"""Fee payments and waivers.

Payments are recorded by staff today. An online gateway can later call
`record_payment(..., method=PaymentMethod.ONLINE, provider="stripe",
reference=<transaction id>)` from its webhook — no other change needed.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core import audit
from apps.core.templatetags.library import money

from .models import Fee, FeeKind, FeeStatus, Payment, PaymentMethod


class FeeError(Exception):
    pass


@transaction.atomic
def record_payment(
    fee: Fee,
    *,
    amount: Decimal,
    method: str = PaymentMethod.CASH,
    paid_on: date | None = None,
    reference: str = "",
    staff=None,
    provider: str = "",
    notify_member: bool = True,
) -> Payment:
    from apps.notifications import notify

    fee = Fee.objects.select_for_update().select_related("member__user").get(pk=fee.pk)
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if fee.status != FeeStatus.PENDING:
        raise FeeError("This fee is already settled.")
    if amount <= 0:
        raise FeeError("Enter an amount greater than zero.")
    if amount > fee.outstanding:
        raise FeeError(f"The amount is more than the outstanding {money(fee.outstanding)}.")
    payment = Payment.objects.create(
        fee=fee,
        amount=amount,
        paid_on=paid_on or timezone.localdate(),
        method=method,
        reference=reference.strip(),
        provider=provider,
        recorded_by=staff,
    )
    fee.amount_paid += amount
    if fee.amount_paid >= fee.amount:
        fee.status = FeeStatus.PAID
        fee.resolved_at = timezone.now()
    fee.save(update_fields=["amount_paid", "status", "resolved_at"])
    audit.record(
        staff,
        "fee.payment",
        f"Recorded {money(amount)} ({payment.get_method_display()}) from {fee.member.user.full_name}"
        + (f", ref {payment.reference}" if payment.reference else ""),
        target=fee,
    )
    if notify_member:
        notify.payment_received(payment)
    return payment


@transaction.atomic
def settle_all(member, *, method: str, reference: str = "", paid_on=None, staff=None) -> list[Payment]:
    fees = list(Fee.objects.select_for_update().filter(member=member, status=FeeStatus.PENDING).order_by("created_at"))
    if not fees:
        raise FeeError("There are no outstanding fees.")
    payments = [
        record_payment(
            fee,
            amount=fee.outstanding,
            method=method,
            reference=reference,
            paid_on=paid_on,
            staff=staff,
            notify_member=False,
        )
        for fee in fees
        if fee.outstanding > 0
    ]
    if payments:
        from apps.notifications import notify

        notify.payment_received(payments[0], total=sum((p.amount for p in payments), Decimal("0")))
    return payments


@transaction.atomic
def waive(fee: Fee, *, reason: str, staff) -> Fee:
    fee = Fee.objects.select_for_update().select_related("member__user").get(pk=fee.pk)
    if fee.status != FeeStatus.PENDING:
        raise FeeError("This fee is already settled.")
    if not reason.strip():
        raise FeeError("Please give a reason for waiving the fee.")
    waived_amount = fee.outstanding
    fee.status = FeeStatus.WAIVED
    fee.waived_by = staff
    fee.waive_reason = reason.strip()[:255]
    fee.resolved_at = timezone.now()
    fee.save(update_fields=["status", "waived_by", "waive_reason", "resolved_at"])
    audit.record(
        staff,
        "fee.waived",
        f"Waived {money(waived_amount)} for {fee.member.user.full_name}: {fee.waive_reason}",
        target=fee,
    )
    return fee


@transaction.atomic
def add_fee(member, *, amount: Decimal, kind: str = FeeKind.OTHER, description: str, staff, loan=None) -> Fee:
    from apps.notifications import notify

    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise FeeError("Enter an amount greater than zero.")
    fee = Fee.objects.create(
        member=member, loan=loan, kind=kind, amount=amount, description=description.strip()[:255], created_by=staff
    )
    audit.record(
        staff, "fee.created", f"Added {money(amount)} fee for {member.user.full_name}: {description}", target=fee
    )
    notify.fee_generated(fee)
    return fee


def outstanding_total(queryset=None) -> Decimal:
    queryset = queryset if queryset is not None else Fee.objects.all()
    totals = queryset.filter(status=FeeStatus.PENDING).aggregate(total=Sum("amount"), paid=Sum("amount_paid"))
    return (totals["total"] or Decimal("0")) - (totals["paid"] or Decimal("0"))
