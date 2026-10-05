from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.core.http import is_partial
from apps.core.permissions import require_capability
from apps.inventory import services
from apps.inventory.models import InventorySession, ScanResult
from apps.staff.forms import InventorySessionForm


@require_capability("inventory")
def inventory_list(request):
    form = InventorySessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        session = form.save(commit=False)
        session.started_by = request.user
        session.save()
        audit.record(request.user, "inventory.started", f"Started inventory '{session.name}'", target=session)
        return redirect("staff:inventory_session", pk=session.pk)
    sessions = InventorySession.objects.select_related("started_by")[:30]
    return render(request, "staff/inventory/list.html", {"form": form, "sessions": sessions})


@require_capability("inventory")
def inventory_session(request, pk):
    session = get_object_or_404(InventorySession, pk=pk)
    if session.status == InventorySession.Status.COMPLETED:
        return redirect("staff:inventory_report", pk=session.pk)
    key = f"inventory_location_{session.pk}"
    last = None
    if request.method == "POST":
        location = request.POST.get("location", request.session.get(key, ""))
        request.session[key] = location
        if request.POST.get("code", "").strip():
            last = services.record_scan(session, request.POST["code"], location=location, staff=request.user)
        if not is_partial(request):
            return redirect("staff:inventory_session", pk=session.pk)
    context = {
        "session": session,
        "location": request.session.get(key, ""),
        "last": last or session.scans.select_related("copy__book").first(),
        "recent": session.scans.select_related("copy__book")[:15],
        "counts": services.counts(session),
        "expected": services.expected_copies(session).count(),
    }
    template = "staff/inventory/_station.html" if is_partial(request) else "staff/inventory/session.html"
    return render(request, template, context)


@require_capability("inventory")
@require_POST
def inventory_complete(request, pk):
    session = get_object_or_404(InventorySession, pk=pk)
    services.complete(session, staff=request.user)
    messages.success(request, "Inventory completed.")
    return redirect("staff:inventory_report", pk=session.pk)


@require_capability("inventory")
def inventory_report(request, pk):
    session = get_object_or_404(InventorySession, pk=pk)
    scans = session.scans.select_related("copy__book")
    missing = services.missing_copies(session) if session.status == InventorySession.Status.OPEN else None
    if missing is None:
        from apps.catalogue.models import BookCopy

        missing = (
            BookCopy.objects.filter(pk__in=session.summary.get("missing_ids", []))
            .select_related("book")
            .order_by("shelf_location")
        )
    context = {
        "session": session,
        "counts": session.summary or services.counts(session),
        "missing": missing,
        "problems": scans.exclude(result=ScanResult.OK),
    }
    return render(request, "staff/inventory/report.html", context)


@require_capability("inventory")
@require_POST
def inventory_mark_lost(request, pk):
    session = get_object_or_404(InventorySession, pk=pk, status=InventorySession.Status.COMPLETED)
    changed = services.mark_missing_as_lost(session, staff=request.user)
    messages.success(request, f"{changed} missing cop{'y' if changed == 1 else 'ies'} marked as lost.")
    return redirect("staff:inventory_report", pk=session.pk)
