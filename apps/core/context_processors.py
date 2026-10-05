from django.conf import settings

from .config import library_settings
from .permissions import capabilities_for


def library(request):
    user = getattr(request, "user", None)
    unread = 0
    if user is not None and user.is_authenticated:
        unread = user.notifications.filter(read_at__isnull=True).count()
    staff_counts = None
    if user is not None and user.is_authenticated and user.is_staff_member and request.path.startswith("/admin"):
        from django.utils import timezone

        from apps.circulation.models import Loan, Reservation, ReservationStatus

        staff_counts = {
            "overdue": Loan.objects.overdue(timezone.localdate()).count(),
            "to_pull": Reservation.objects.filter(status=ReservationStatus.READY, picked_at__isnull=True).count(),
        }
    return {
        "staff_counts": staff_counts,
        "library": library_settings(),
        "caps": capabilities_for(user),
        "unread_notifications": unread,
        "site_url": settings.SITE_URL,
        "debug": settings.DEBUG,
    }
