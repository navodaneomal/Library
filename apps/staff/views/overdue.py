from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.circulation.models import Loan
from apps.core import audit
from apps.core.permissions import require_capability
from apps.core.tasks import run_all


@require_capability("overdue.view")
def overdue_list(request):
    today = timezone.localdate()
    loans = Loan.objects.overdue(today).select_related("copy__book", "member__user").order_by("due_date")
    rows = [{"loan": loan, "quote": loan.fee_quote(on=today)} for loan in loans]
    total = sum(row["quote"].amount for row in rows)
    return render(request, "staff/overdue.html", {"rows": rows, "total": total, "today": today})


@require_capability("reminders.send")
@require_POST
def run_tasks(request):
    results = run_all()
    audit.record(
        request.user,
        "system.tasks",
        "Ran scheduled tasks manually",
        changes={"results": {k: str(v) for k, v in results.items()}},
    )
    messages.success(
        request,
        f"Done — {results.get('due_soon_reminders', 0)} due-soon and {results.get('overdue_reminders', 0)} overdue loans checked; "
        "members who haven't had today's reminder were notified.",
    )
    return redirect("staff:overdue")
