from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.circulation import reservations as reservation_service
from apps.circulation.models import Reservation, ReservationStatus
from apps.core.http import safe_next
from apps.core.permissions import require_capability


@require_capability("reservations.manage")
def reservation_list(request):
    ready = Reservation.objects.filter(status=ReservationStatus.READY).select_related("book", "member__user", "copy")
    waiting = (
        Reservation.objects.filter(status=ReservationStatus.WAITING)
        .select_related("book", "member__user")
        .order_by("book__title", "created_at", "id")
    )
    waiting_rows, position, last_book = [], 0, None
    for reservation in waiting:
        position = position + 1 if reservation.book_id == last_book else 1
        last_book = reservation.book_id
        waiting_rows.append({"reservation": reservation, "position": position})
    closed = Paginator(
        Reservation.objects.exclude(status__in=[ReservationStatus.READY, ReservationStatus.WAITING])
        .select_related("book", "member__user")
        .order_by("-closed_at", "-created_at"),
        20,
    ).get_page(request.GET.get("page"))
    context = {
        "to_pull": ready.filter(picked_at__isnull=True).order_by("copy__shelf_location"),
        "on_shelf": ready.filter(picked_at__isnull=False).order_by("expires_at"),
        "waiting_rows": waiting_rows,
        "closed": closed,
        "now": timezone.now(),
    }
    return render(request, "staff/reservations/list.html", context)


@require_capability("reservations.manage")
@require_POST
def reservation_picked(request, pk):
    reservation = get_object_or_404(Reservation, pk=pk)
    reservation_service.mark_picked(reservation, actor=request.user)
    messages.success(
        request, f"'{reservation.book.title}' is on the hold shelf for {reservation.member.user.full_name}."
    )
    return redirect(safe_next(request, reverse("staff:reservations")))


@require_capability("reservations.manage")
@require_POST
def reservation_cancel(request, pk):
    reservation = get_object_or_404(Reservation.objects.select_related("book", "member__user"), pk=pk)
    try:
        reservation_service.cancel(reservation, actor=request.user, reason=request.POST.get("reason", "").strip()[:200])
    except reservation_service.ReservationError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request, f"Cancelled {reservation.member.user.full_name}'s reservation of '{reservation.book.title}'."
        )
    return redirect(safe_next(request, reverse("staff:reservations")))
