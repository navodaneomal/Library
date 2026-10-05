from django.conf import settings
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from apps.circulation.models import OPEN_RESERVATION_STATUSES, Loan, Reservation
from apps.circulation.rules import member_standing, renewal_problems
from apps.core import audit
from apps.core.permissions import require_capability
from apps.fees.models import Fee
from apps.members.models import Member, MemberStatus
from apps.members.services import create_member
from apps.notifications import notify
from apps.notifications.models import EmailLog
from apps.staff.forms import AddFeeForm, MemberForm, MemberStatusForm, SettleForm


def _send_set_password_email(request, user):
    """Invite link that lets a desk-registered member choose a password."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.SITE_URL}{reverse('accounts:password_reset_confirm', args=[uid, token])}"
    notify.set_password_invite(user, link)


@require_capability("members.view")
def member_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    members = Member.objects.select_related("user").annotate(
        active_loans=Count("loans", filter=Q(loans__status="active"), distinct=True)
    )
    if query:
        compact = query.replace(" ", "")
        members = members.filter(
            Q(member_id__iexact=query)
            | Q(member_id__icontains=query)
            | Q(user__full_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(phone__icontains=compact)
        )
        exact = members.filter(member_id__iexact=query).first()
        if exact and request.GET.get("go") != "0":
            return redirect("staff:member", pk=exact.pk)
    if status in MemberStatus.values:
        members = members.filter(status=status)
    page = Paginator(members.order_by("user__full_name"), 30).get_page(request.GET.get("page"))
    return render(
        request,
        "staff/members/list.html",
        {
            "page": page,
            "query": query,
            "status": status,
            "statuses": MemberStatus.choices,
            "total": Member.objects.count(),
        },
    )


@require_capability("members.view")
def member_detail(request, pk):
    member = get_object_or_404(Member.objects.select_related("user"), pk=pk)
    today = timezone.localdate()
    standing = member_standing(member, today=today)
    context = {
        "member": member,
        "standing": standing,
        "loans": [
            {"loan": loan, "quote": loan.fee_quote(on=today), "renew_problems": renewal_problems(loan, today=today)}
            for loan in standing.active_loans
        ],
        "reservations": Reservation.objects.filter(member=member, status__in=OPEN_RESERVATION_STATUSES).select_related(
            "book", "copy"
        ),
        "history": Loan.objects.filter(member=member)
        .exclude(status="active")
        .select_related("copy__book")
        .order_by("-borrowed_at")[:30],
        "fees": Fee.objects.filter(member=member).select_related("loan__copy__book").prefetch_related("payments")[:30],
        "emails": EmailLog.objects.filter(user=member.user).only("template", "subject", "status", "created_at")[:10],
        "status_form": MemberStatusForm(initial={"status": member.status, "reason": member.status_reason}),
        "settle_form": SettleForm(),
        "fee_form": AddFeeForm(),
    }
    return render(request, "staff/members/detail.html", context)


@require_capability("members.manage")
def member_new(request):
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            member = create_member(
                full_name=data["full_name"],
                email=data["email"],
                phone=data["phone"],
                address=data["address"],
                date_of_birth=data["date_of_birth"],
                created_by=request.user,
            )
        except IntegrityError:
            form.add_error("email", "Another account already uses this email.")
        else:
            member.email_reminders = data["email_reminders"]
            member.staff_notes = data["staff_notes"]
            member.save(update_fields=["email_reminders", "staff_notes"])
            notify.welcome(member)
            if member.user.email and data.get("send_invite"):
                _send_set_password_email(request, member.user)
            audit.record(request.user, "member.created", f"Registered {member}", target=member, request=request)
            messages.success(request, f"Member registered — {member.member_id}. Their library card is ready to print.")
            return redirect("staff:member", pk=member.pk)
    return render(request, "staff/members/form.html", {"form": form, "is_new": True})


@require_capability("members.manage")
def member_edit(request, pk):
    member = get_object_or_404(Member.objects.select_related("user"), pk=pk)
    initial = {
        "full_name": member.user.full_name,
        "email": member.user.email,
        "phone": member.phone,
        "address": member.address,
        "date_of_birth": member.date_of_birth,
        "email_reminders": member.email_reminders,
        "staff_notes": member.staff_notes,
    }
    form = MemberForm(request.POST or None, initial=initial, member=member)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user = member.user
            email_changed = (user.email or None) != (data["email"] or None)
            user.full_name, user.email = data["full_name"].strip(), data["email"]
            if email_changed:
                user.email_verified_at = None
            user.save()
            for field in ("phone", "address", "date_of_birth", "email_reminders", "staff_notes"):
                setattr(member, field, data[field])
            member.save()
        changes = audit.diff(
            {k: initial[k] for k in initial if k != "staff_notes"}, {k: data[k] for k in initial if k != "staff_notes"}
        )
        audit.record(
            request.user, "member.updated", f"Edited {member}", target=member, changes=changes, request=request
        )
        messages.success(request, "Member details saved.")
        return redirect("staff:member", pk=member.pk)
    return render(request, "staff/members/form.html", {"form": form, "member": member, "is_new": False})


@require_capability("members.manage")
@require_POST
def member_status(request, pk):
    member = get_object_or_404(Member.objects.select_related("user"), pk=pk)
    form = MemberStatusForm(request.POST)
    if form.is_valid():
        old = member.status
        member.status = form.cleaned_data["status"]
        member.status_reason = form.cleaned_data["reason"]
        member.save(update_fields=["status", "status_reason", "updated_at"])
        audit.record(
            request.user,
            "member.status",
            f"{member}: {old} → {member.status}" + (f" ({member.status_reason})" if member.status_reason else ""),
            target=member,
            request=request,
        )
        messages.success(request, f"Membership is now {member.get_status_display().lower()}.")
    return redirect("staff:member", pk=member.pk)


@require_capability("members.manage")
@require_POST
def member_send_password(request, pk):
    member = get_object_or_404(Member.objects.select_related("user"), pk=pk)
    if not member.user.email:
        messages.error(request, "This member has no email address.")
    else:
        _send_set_password_email(request, member.user)
        audit.record(
            request.user, "member.password_link", f"Sent a password link to {member}", target=member, request=request
        )
        messages.success(request, f"A link to set a password was sent to {member.user.email}.")
    return redirect("staff:member", pk=member.pk)


@require_capability("members.view")
def member_card(request, pk):
    member = get_object_or_404(Member.objects.select_related("user"), pk=pk)
    return render(request, "staff/members/card.html", {"member": member})
