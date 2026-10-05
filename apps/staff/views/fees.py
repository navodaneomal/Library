from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.http import safe_next
from apps.core.permissions import require_capability
from apps.core.templatetags.library import money
from apps.fees import services as fee_service
from apps.fees.models import Fee, FeeStatus
from apps.members.models import Member
from apps.staff.forms import AddFeeForm, PaymentForm, SettleForm, WaiveForm


@require_capability("fees.view")
def fee_list(request):
    status = request.GET.get("status", FeeStatus.PENDING)
    query = request.GET.get("q", "").strip()
    fees = Fee.objects.select_related("member__user", "loan__copy__book")
    if status in FeeStatus.values:
        fees = fees.filter(status=status)
    if query:
        fees = fees.filter(Q(member__member_id__iexact=query) | Q(member__user__full_name__icontains=query))
    page = Paginator(fees.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(
        request,
        "staff/fees/list.html",
        {
            "page": page,
            "status": status,
            "query": query,
            "statuses": FeeStatus.choices,
            "outstanding": fee_service.outstanding_total(),
        },
    )


@require_capability("fees.view")
def fee_detail(request, pk):
    fee = get_object_or_404(Fee.objects.select_related("member__user", "loan__copy__book"), pk=pk)
    return render(
        request,
        "staff/fees/detail.html",
        {
            "fee": fee,
            "payments": fee.payments.select_related("recorded_by"),
            "payment_form": PaymentForm(initial={"amount": fee.outstanding}),
            "waive_form": WaiveForm(),
        },
    )


@require_capability("fees.manage")
@require_POST
def fee_pay(request, pk):
    fee = get_object_or_404(Fee, pk=pk)
    form = PaymentForm(request.POST)
    if form.is_valid():
        try:
            payment = fee_service.record_payment(
                fee,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                paid_on=form.cleaned_data["paid_on"],
                reference=form.cleaned_data["reference"],
                staff=request.user,
            )
        except fee_service.FeeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Payment of {money(payment.amount)} recorded.")
    else:
        messages.error(request, "Please check the payment details.")
    return redirect(safe_next(request, reverse("staff:fee", args=[fee.pk])))


@require_capability("fees.manage")
@require_POST
def fee_waive(request, pk):
    fee = get_object_or_404(Fee, pk=pk)
    form = WaiveForm(request.POST)
    try:
        if not form.is_valid():
            raise fee_service.FeeError("Please give a reason for waiving the fee.")
        fee_service.waive(fee, reason=form.cleaned_data["reason"], staff=request.user)
    except fee_service.FeeError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Fee waived.")
    return redirect(safe_next(request, reverse("staff:fee", args=[fee.pk])))


@require_capability("fees.manage")
@require_POST
def member_settle(request, pk):
    member = get_object_or_404(Member, pk=pk)
    form = SettleForm(request.POST)
    if form.is_valid():
        try:
            payments = fee_service.settle_all(
                member,
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                paid_on=form.cleaned_data["paid_on"],
                staff=request.user,
            )
        except fee_service.FeeError as error:
            messages.error(request, str(error))
        else:
            total = sum((p.amount for p in payments), Decimal("0"))
            messages.success(request, f"Recorded {money(total)} — all fees are now paid.")
    return redirect("staff:member", pk=member.pk)


@require_capability("fees.manage")
@require_POST
def member_add_fee(request, pk):
    member = get_object_or_404(Member, pk=pk)
    form = AddFeeForm(request.POST)
    if form.is_valid():
        try:
            fee_service.add_fee(
                member,
                amount=form.cleaned_data["amount"],
                kind=form.cleaned_data["kind"],
                description=form.cleaned_data["description"],
                staff=request.user,
            )
        except fee_service.FeeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Fee added and the member was notified.")
    else:
        messages.error(request, "Please enter an amount and a description.")
    return redirect("staff:member", pk=member.pk)
