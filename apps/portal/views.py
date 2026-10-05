"""Member portal — every view only ever touches the signed-in member's own data."""

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.catalogue.availability import annotate_availability
from apps.catalogue.models import Book
from apps.catalogue.recommendations import get_recommender
from apps.circulation import reservations as reservation_service
from apps.circulation.models import (
    OPEN_RESERVATION_STATUSES,
    Loan,
    LoanStatus,
    Reservation,
)
from apps.circulation.rules import member_standing, renewal_problems
from apps.circulation.services import CirculationError, renew
from apps.core.permissions import member_required
from apps.fees.models import Fee, FeeStatus
from apps.members.models import Member
from apps.notifications.models import Notification


def _reservation_rows(reservations):
    return [
        {
            "reservation": r,
            "position": reservation_service.queue_position(r),
            "estimate": reservation_service.estimated_availability(r),
        }
        for r in reservations
    ]


def _loan_rows(loans, today):
    return [
        {"loan": loan, "quote": loan.fee_quote(on=today), "renew_problems": renewal_problems(loan, today=today)}
        for loan in loans
    ]


@member_required
def dashboard(request):
    member = request.user.member
    today = timezone.localdate()
    standing = member_standing(member, today=today)
    open_reservations = list(
        Reservation.objects.filter(member=member, status__in=OPEN_RESERVATION_STATUSES)
        .select_related("book", "book__category")
        .order_by("status", "created_at")
    )
    current_book_ids = {loan.copy.book_id for loan in standing.active_loans}
    previous = (
        Loan.objects.filter(member=member)
        .exclude(status=LoanStatus.ACTIVE)
        .values("copy__book")
        .annotate(last=Max("borrowed_at"))
        .order_by("-last")[:12]
    )
    previous_ids = [row["copy__book"] for row in previous if row["copy__book"] not in current_book_ids][:6]
    previous_books = annotate_availability(Book.objects.filter(pk__in=previous_ids, is_archived=False)).select_related(
        "category"
    )
    previous_books = sorted(previous_books, key=lambda b: previous_ids.index(b.pk))
    context = {
        "member": member,
        "standing": standing,
        "loans": _loan_rows(standing.active_loans, today),
        "reservations": _reservation_rows(open_reservations),
        "previous_books": previous_books,
        "recommended": get_recommender().for_member(member, limit=4),
        "pending_fees": Fee.objects.filter(member=member, status=FeeStatus.PENDING).select_related("loan__copy__book")[
            :5
        ],
        "recent_notifications": request.user.notifications.all()[:3],
    }
    return render(request, "portal/dashboard.html", context)


@member_required
def my_books(request):
    member = request.user.member
    today = timezone.localdate()
    active = (
        Loan.objects.filter(member=member, status=LoanStatus.ACTIVE)
        .select_related("copy__book", "copy__book__category")
        .order_by("due_date")
    )
    history = (
        Loan.objects.filter(member=member)
        .exclude(status=LoanStatus.ACTIVE)
        .select_related("copy__book", "copy__book__category")
        .prefetch_related("fees")
        .order_by("-borrowed_at")
    )
    page = Paginator(history, 20).get_page(request.GET.get("page"))
    return render(request, "portal/my_books.html", {"loans": _loan_rows(active, today), "page": page})


@member_required
@require_POST
def renew_loan(request, pk):
    loan = get_object_or_404(Loan, pk=pk, member=request.user.member)
    try:
        loan = renew(loan, actor=request.user)
    except CirculationError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request, f"Renewed — '{loan.copy.book.title}' is now due {loan.due_date.day} {loan.due_date:%B}."
        )
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "portal:my_books"
    return redirect(target)


@member_required
def reservations(request):
    member = request.user.member
    open_rows = _reservation_rows(
        Reservation.objects.filter(member=member, status__in=OPEN_RESERVATION_STATUSES)
        .select_related("book", "book__category")
        .order_by("status", "created_at")
    )
    past = (
        Reservation.objects.filter(member=member)
        .exclude(status__in=OPEN_RESERVATION_STATUSES)
        .select_related("book")
        .order_by("-created_at")[:30]
    )
    return render(request, "portal/reservations.html", {"open_rows": open_rows, "past": past})


@member_required
def reservation_detail(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.select_related("book", "book__category"), pk=pk, member=request.user.member
    )
    context = {
        "reservation": reservation,
        "position": reservation_service.queue_position(reservation),
        "estimate": reservation_service.estimated_availability(reservation),
        "is_new": request.GET.get("new") == "1",
    }
    return render(request, "portal/reservation_detail.html", context)


@member_required
@require_POST
def cancel_reservation(request, pk):
    reservation = get_object_or_404(Reservation, pk=pk, member=request.user.member)
    try:
        reservation_service.cancel(reservation, actor=request.user)
    except reservation_service.ReservationError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Reservation for '{reservation.book.title}' cancelled.")
    return redirect("portal:reservations")


@member_required
def fees(request):
    member = request.user.member
    today = timezone.localdate()
    standing = member_standing(member, today=today)
    pending = Fee.objects.filter(member=member, status=FeeStatus.PENDING).select_related("loan__copy__book")
    settled = (
        Fee.objects.filter(member=member)
        .exclude(status=FeeStatus.PENDING)
        .select_related("loan__copy__book")
        .prefetch_related("payments")[:30]
    )
    accruing = [(loan, loan.fee_quote(on=today)) for loan in standing.overdue_loans]
    return render(
        request,
        "portal/fees.html",
        {"standing": standing, "pending": pending, "settled": settled, "accruing": accruing},
    )


@member_required
def notifications(request):
    page = Paginator(request.user.notifications.all(), 25).get_page(request.GET.get("page"))
    return render(request, "portal/notifications.html", {"page": page})


@member_required
def open_notification(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if not notification.read_at:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    target = notification.url if notification.url.startswith("/") and not notification.url.startswith("//") else ""
    return redirect(target or "portal:notifications")


@member_required
@require_POST
def mark_notifications_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    messages.success(request, "All caught up.")
    return redirect("portal:notifications")


class ProfileForm(forms.ModelForm):
    full_name = forms.CharField(max_length=150)

    class Meta:
        model = Member
        fields = ["phone", "address", "email_reminders"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].initial = self.instance.user.full_name
        self.order_fields(["full_name", "phone", "address", "email_reminders"])

    def save(self, commit=True):
        member = super().save(commit=commit)
        member.user.full_name = self.cleaned_data["full_name"].strip()
        member.user.save(update_fields=["full_name"])
        return member


@member_required
def profile(request):
    member = request.user.member
    form = ProfileForm(request.POST or None, instance=member)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("portal:profile")
    return render(request, "portal/profile.html", {"form": form, "member": member})


@member_required
def card(request):
    return render(request, "portal/card.html", {"member": request.user.member})
