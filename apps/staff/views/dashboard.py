from django.shortcuts import render
from django.utils import timezone

from apps.circulation.models import Loan, LoanStatus, Reservation, ReservationStatus
from apps.core.permissions import require_capability
from apps.reports import queries


@require_capability("staff.access")
def dashboard(request):
    today = timezone.localdate()
    context = {
        "metrics": queries.library_metrics(today),
        "activity": queries.today_activity(today),
        "insights": queries.insights(today),
        "recent_loans": Loan.objects.select_related("copy__book", "member__user").order_by("-borrowed_at")[:6],
        "recent_returns": Loan.objects.filter(status=LoanStatus.RETURNED)
        .select_related("copy__book", "member__user")
        .order_by("-returned_at")[:6],
    }
    return render(request, "staff/dashboard.html", context)


@require_capability("staff.access")
def today_view(request):
    today = timezone.localdate()
    active = Loan.objects.active().select_related("copy__book", "member__user")
    ready = Reservation.objects.filter(status=ReservationStatus.READY).select_related("book", "member__user", "copy")
    context = {
        "today": today,
        "due_today": active.filter(due_date=today).order_by("member__user__full_name"),
        "overdue": active.filter(due_date__lt=today).order_by("due_date")[:15],
        "overdue_count": active.filter(due_date__lt=today).count(),
        "to_pull": ready.filter(picked_at__isnull=True).order_by("copy__shelf_location"),
        "on_hold_shelf": ready.filter(picked_at__isnull=False).order_by("expires_at"),
        "expiring_today": ready.filter(expires_at__date=today).count(),
        "activity": queries.today_activity(today),
    }
    return render(request, "staff/today.html", context)
