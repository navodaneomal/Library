"""Shared circulation-desk logic for the Circulation page and Quick Scan.

Desk state lives in the staff member's session (JSON-safe values only), so
a page reload never loses the member currently being served.
"""

from django.db.models import Q

from apps.catalogue.models import CopyStatus
from apps.catalogue.services import ScanLookup, lookup_code
from apps.catalogue.shelves import describe
from apps.circulation.models import Reservation, ReservationStatus
from apps.circulation.rules import check_borrow
from apps.circulation.services import (
    CirculationError,
    active_loan_for,
    borrow,
    return_copy,
)
from apps.core.templatetags.library import money, nice_date, short_date
from apps.members.models import Member

RECENT_LIMIT = 12


def result(
    tone: str, title: str, *, subtitle: str = "", details=None, problems=None, hint: str = "", sound: str | None = None
):
    """A display-ready desk message. tone: success | ready | error | warning | info."""
    return {
        "tone": tone,
        "title": title,
        "subtitle": subtitle,
        "details": details or [],
        "problems": problems or [],
        "hint": hint,
        "sound": sound or ("error" if tone in ("error", "warning") else "success"),
    }


class DeskState:
    def __init__(self, session, key: str = "desk"):
        self.session = session
        self.key = key
        self.data = session.get(key) or {"member_id": None, "pending_copy_id": None, "result": None, "recent": []}

    def save(self):
        self.session[self.key] = self.data
        self.session.modified = True

    @property
    def member(self) -> Member | None:
        member_id = self.data.get("member_id")
        if not member_id:
            return None
        return Member.objects.select_related("user").filter(pk=member_id).first()

    def set_member(self, member: Member | None):
        self.data["member_id"] = member.pk if member else None
        self.data["pending_copy_id"] = None
        if member is None:
            self.data["recent"] = []

    def set_result(self, value):
        self.data["result"] = value

    def push_recent(self, item: dict):
        self.data["recent"] = ([item] + list(self.data.get("recent", [])))[:RECENT_LIMIT]

    def clear(self):
        self.data = {"member_id": None, "pending_copy_id": None, "result": None, "recent": []}


def find_members(query: str, limit: int = 8):
    query = (query or "").strip()
    if not query:
        return []
    exact = Member.objects.select_related("user").filter(member_id__iexact=query).first()
    if exact:
        return [exact]
    return list(
        Member.objects.select_related("user")
        .filter(
            Q(user__full_name__icontains=query)
            | Q(user__email__iexact=query)
            | Q(phone__icontains=query.replace(" ", ""))
        )
        .order_by("user__full_name")[:limit]
    )


def member_loaded_result(member: Member, standing) -> dict:
    details = [
        ["Member ID", member.member_id],
        ["Borrowed", str(len(standing.active_loans))],
        ["Overdue", str(len(standing.overdue_loans))],
        ["Unpaid fees", money(standing.outstanding_fees)],
    ]
    if standing.blocks:
        return result(
            "warning",
            member.user.full_name,
            subtitle="Borrowing unavailable",
            details=details,
            problems=[p.message for p in standing.blocks],
            hint="Books can still be returned.",
        )
    ready = standing.ready_reservations
    hint = ""
    if ready:
        titles = ", ".join(r.book.title for r in ready)
        hint = f"Waiting on the hold shelf: {titles}"
    return result("info", member.user.full_name, subtitle="Ready — scan a book", details=details, hint=hint)


def check_result(check) -> dict:
    copy, member = check.copy, check.member
    if check.ok:
        details = [
            ["Borrower", member.user.full_name],
            ["Due date", nice_date(check.due_date)],
            ["Copy", copy.copy_code],
        ]
        if check.reservation:
            details.append(["Reservation", "Will be marked as collected"])
        return result(
            "ready",
            "Ready to borrow",
            subtitle=copy.book.title,
            details=details,
            hint="Press Enter or Confirm to lend it.",
        )
    return result(
        "error",
        "Borrowing unavailable",
        subtitle=copy.book.title,
        problems=[p.message for p in check.blocks],
    )


def scan_for_borrow(state: DeskState, lookup: ScanLookup) -> None:
    """Handle a scan on the borrow desk: a member card or a copy barcode."""
    if lookup.kind == "member":
        from apps.circulation.rules import member_standing

        state.set_member(lookup.member)
        state.set_result(member_loaded_result(lookup.member, member_standing(lookup.member)))
        return
    member = state.member
    if lookup.kind == "copy":
        if member is None:
            loan = active_loan_for(lookup.copy)
            hint = "This copy is on loan — use Return to check it in." if loan else ""
            state.set_result(
                result("error", "Scan the member's card first", subtitle=lookup.copy.book.title, hint=hint)
            )
            return
        check = check_borrow(member, lookup.copy)
        state.data["pending_copy_id"] = lookup.copy.pk if check.ok else None
        state.set_result(check_result(check))
        return
    state.data["pending_copy_id"] = None
    state.set_result(unrecognised(lookup))


def unrecognised(lookup: ScanLookup) -> dict:
    if lookup.kind == "book":
        return result(
            "warning",
            "That's the book's ISBN",
            subtitle=lookup.book.title,
            hint="Scan the library barcode label on the copy instead.",
        )
    if lookup.kind == "new_isbn":
        return result(
            "error",
            "This book isn't in the catalogue",
            subtitle=f"ISBN {lookup.isbn}",
            hint="Add it under Books → Add book.",
        )
    if lookup.kind == "empty":
        return result("info", "Waiting for a scan")
    return result("error", "Barcode not recognised", subtitle=lookup.code, hint="Check the label or type the code.")


def confirm_borrow(state: DeskState, copy_pk: int, staff) -> None:
    from apps.catalogue.models import BookCopy

    member = state.member
    pending = state.data.get("pending_copy_id")
    if member is None or not pending or int(copy_pk or 0) != int(pending):
        state.set_result(result("error", "Nothing to confirm", hint="Scan a book first."))
        return
    copy = BookCopy.objects.select_related("book").get(pk=pending)
    try:
        loan = borrow(member, copy, staff=staff)
    except CirculationError as error:
        state.set_result(
            result(
                "error",
                "Borrowing unavailable",
                subtitle=copy.book.title,
                problems=[p.message for p in error.problems] or [error.message],
            )
        )
    else:
        state.set_result(
            result(
                "success",
                "Done — book lent",
                subtitle=copy.book.title,
                details=[["Borrower", member.user.full_name], ["Due date", nice_date(loan.due_date)]],
                hint="Scan the next book, or finish.",
            )
        )
        state.push_recent(
            {"title": copy.book.title, "copy": copy.copy_code, "due": short_date(loan.due_date), "kind": "borrowed"}
        )
    state.data["pending_copy_id"] = None


def return_result(outcome) -> dict:
    loan, copy = outcome.loan, outcome.copy
    details = [
        ["Borrowed", short_date(outcome.borrowed_on)],
        ["Due", short_date(outcome.due_date)],
        ["Returned", short_date(outcome.returned_on)],
        ["Days borrowed", str(outcome.days_borrowed)],
        [
            "Overdue",
            f"{outcome.days_overdue} day{'s' if outcome.days_overdue != 1 else ''}" if outcome.days_overdue else "No",
        ],
        ["Late fee", money(outcome.fee.amount) if outcome.fee else money(0)],
    ]
    if outcome.hold:
        hold_member = outcome.hold.member
        hint = f"Place on the HOLD SHELF for {hold_member.user.full_name} ({hold_member.member_id})."
        tone = "warning"
    else:
        steps = describe(copy.shelf_location)
        hint = f"Back to shelf: {' → '.join(steps)}" if steps else "Back to the shelf."
        tone = "success"
    return result(
        tone,
        "Book returned",
        subtitle=f"{copy.book.title} · {loan.member.user.full_name}",
        details=details,
        hint=hint,
        sound="success" if not outcome.fee else "warning",
    )


def not_on_loan_result(copy) -> dict:
    if copy.status == CopyStatus.RESERVED:
        hold = (
            Reservation.objects.filter(copy=copy, status=ReservationStatus.READY).select_related("member__user").first()
        )
        who = f"{hold.member.user.full_name} ({hold.member.member_id})" if hold else "a member"
        return result(
            "warning", "Not on loan — on hold", subtitle=copy.book.title, hint=f"Keep it on the hold shelf for {who}."
        )
    steps = describe(copy.shelf_location)
    return result(
        "info",
        "This copy isn't on loan",
        subtitle=copy.book.title,
        details=[["Status", copy.get_status_display()], ["Copy", copy.copy_code]],
        hint=f"Shelf: {' → '.join(steps)}" if steps else "",
        sound="error",
    )


def process_return(state: DeskState, lookup: ScanLookup, staff) -> None:
    if lookup.kind != "copy":
        if lookup.kind == "member":
            state.set_result(result("warning", "That's a library card", hint="Scan the book's barcode to return it."))
        else:
            state.set_result(unrecognised(lookup))
        return
    try:
        outcome = return_copy(lookup.copy, staff=staff)
    except CirculationError:
        state.set_result(not_on_loan_result(lookup.copy))
        return
    state.set_result(return_result(outcome))
    state.push_recent(
        {
            "title": lookup.copy.book.title,
            "copy": lookup.copy.copy_code,
            "member": outcome.loan.member.user.full_name,
            "fee": money(outcome.fee.amount) if outcome.fee else "",
            "hold": outcome.hold.member.user.full_name if outcome.hold else "",
            "kind": "returned",
        }
    )


def lookup(code: str) -> ScanLookup:
    return lookup_code(code)
