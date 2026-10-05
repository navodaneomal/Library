"""Inventory mode: walk the shelves scanning barcodes; the system works out
what is missing, misplaced, scanned twice or in an unexpected state."""

from django.db import transaction
from django.utils import timezone

from apps.barcodes.scan import normalize_code
from apps.catalogue.models import BookCopy, CopyStatus
from apps.core import audit

from .models import InventoryScan, InventorySession, ScanResult

# Copies we expect to find on the open shelves.
EXPECTED_ON_SHELF = (CopyStatus.AVAILABLE,)


def in_scope(session: InventorySession, shelf: str) -> bool:
    return not session.scope_prefix or (shelf or "").upper().startswith(session.scope_prefix.upper())


def record_scan(session: InventorySession, raw_code: str, *, location: str, staff) -> InventoryScan:
    code = normalize_code(raw_code)
    location = normalize_code(location)
    copy = BookCopy.objects.select_related("book").filter(barcode=code).first()
    if copy is None:
        return InventoryScan.objects.create(
            session=session,
            barcode=code,
            location=location,
            result=ScanResult.UNKNOWN,
            note="This barcode is not registered.",
            scanned_by=staff,
        )
    if session.scans.filter(copy=copy).exists():
        result, note = ScanResult.DUPLICATE, "Already scanned in this inventory."
    elif copy.status not in (CopyStatus.AVAILABLE, CopyStatus.RESERVED):
        result = ScanResult.STATUS_MISMATCH
        note = f"Recorded as {copy.get_status_display().lower()} but found on the shelf."
    elif location and copy.shelf_location and not copy.shelf_location.upper().startswith(location):
        result, note = ScanResult.UNEXPECTED_LOCATION, f"Belongs on {copy.shelf_location}."
    elif not in_scope(session, copy.shelf_location):
        result, note = ScanResult.UNEXPECTED_LOCATION, f"Belongs on {copy.shelf_location or 'an unassigned shelf'}."
    else:
        result, note = ScanResult.OK, copy.shelf_location
    return InventoryScan.objects.create(
        session=session, barcode=code, copy=copy, location=location, result=result, note=note, scanned_by=staff
    )


def expected_copies(session: InventorySession):
    copies = BookCopy.objects.filter(status__in=EXPECTED_ON_SHELF, book__is_archived=False)
    if session.scope_prefix:
        copies = copies.filter(shelf_location__istartswith=session.scope_prefix)
    return copies


def missing_copies(session: InventorySession):
    scanned_ids = session.scans.exclude(copy=None).values_list("copy_id", flat=True)
    return expected_copies(session).exclude(pk__in=scanned_ids).select_related("book").order_by("shelf_location")


def counts(session: InventorySession) -> dict:
    data = {choice: 0 for choice, _ in ScanResult.choices}
    for result in session.scans.values_list("result", flat=True):
        data[result] += 1
    data["scanned"] = sum(data.values())
    return data


@transaction.atomic
def complete(session: InventorySession, *, staff) -> InventorySession:
    session = InventorySession.objects.select_for_update().get(pk=session.pk)
    if session.status == InventorySession.Status.COMPLETED:
        return session
    summary = counts(session)
    summary["missing"] = missing_copies(session).count()
    summary["missing_ids"] = list(missing_copies(session).values_list("pk", flat=True)[:5000])
    session.summary = summary
    session.status = InventorySession.Status.COMPLETED
    session.completed_by = staff
    session.completed_at = timezone.now()
    session.save()
    audit.record(
        staff,
        "inventory.completed",
        f"Completed inventory '{session.name}': {summary['scanned']} scanned, {summary['missing']} missing",
        target=session,
    )
    return session


@transaction.atomic
def mark_missing_as_lost(session: InventorySession, *, staff) -> int:
    from apps.circulation.services import change_copy_state

    ids = session.summary.get("missing_ids", [])
    changed = 0
    for copy in BookCopy.objects.filter(pk__in=ids, status__in=EXPECTED_ON_SHELF):
        change_copy_state(copy, status=CopyStatus.LOST, condition="lost", actor=staff)
        changed += 1
    audit.record(
        staff,
        "inventory.marked_lost",
        f"Marked {changed} missing copies as lost after '{session.name}'",
        target=session,
    )
    return changed
