"""Report queries. Each report returns a Table (columns + rows of plain
values) that is rendered as HTML or exported to CSV by the same code."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.catalogue.models import Book, BookCopy, CopyCondition, CopyStatus
from apps.circulation.models import (
    OPEN_RESERVATION_STATUSES,
    Loan,
    LoanStatus,
    Reservation,
)
from apps.fees.models import Fee, FeeStatus, Payment
from apps.fees.services import outstanding_total
from apps.inventory.models import InventorySession


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list] = field(default_factory=list)
    totals: list[tuple[str, object]] = field(default_factory=list)
    money_columns: set[int] = field(default_factory=set)
    note: str = ""


def default_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = timezone.localdate()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=29))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def borrowing(date_from, date_to, *, member_query: str = "", category: str = "") -> Table:
    date_from, date_to = default_range(date_from, date_to)
    loans = (
        Loan.objects.filter(borrowed_at__date__gte=date_from, borrowed_at__date__lte=date_to)
        .select_related("copy__book__category", "member__user")
        .order_by("-borrowed_at")
    )
    if member_query:
        loans = loans.filter(
            Q(member__member_id__iexact=member_query) | Q(member__user__full_name__icontains=member_query)
        )
    if category:
        loans = loans.filter(copy__book__category__slug=category)
    table = Table(
        "Borrowing report",
        ["Borrowed", "Book", "Category", "Copy", "Member", "Member ID", "Due", "Returned", "Status"],
    )
    for loan in loans[:5000]:
        book = loan.copy.book
        table.rows.append(
            [
                loan.borrowed_on,
                book.title,
                book.category.name if book.category_id else "",
                loan.copy.copy_code,
                loan.member.user.full_name,
                loan.member.member_id,
                loan.due_date,
                loan.returned_on or "",
                "Overdue" if loan.is_overdue() else loan.get_status_display(),
            ]
        )
    table.totals = [("Loans", len(table.rows)), ("Period", f"{date_from:%d %b %Y} – {date_to:%d %b %Y}")]
    return table


def overdue(today: date | None = None) -> Table:
    today = today or timezone.localdate()
    table = Table(
        "Overdue report",
        ["Member", "Member ID", "Phone", "Book", "Copy", "Due", "Days overdue", "Estimated fee"],
        money_columns={7},
    )
    total = Decimal("0")
    for loan in Loan.objects.overdue(today).select_related("copy__book", "member__user").order_by("due_date"):
        quote = loan.fee_quote(on=today)
        total += quote.amount
        table.rows.append(
            [
                loan.member.user.full_name,
                loan.member.member_id,
                loan.member.phone,
                loan.copy.book.title,
                loan.copy.copy_code,
                loan.due_date,
                quote.days_overdue,
                quote.amount,
            ]
        )
    table.totals = [("Overdue loans", len(table.rows)), ("Estimated fees", total)]
    table.note = "Estimated fees keep growing until the books are returned."
    return table


def popular(date_from, date_to, limit: int = 50) -> Table:
    date_from, date_to = default_range(date_from, date_to)
    books = (
        Book.objects.annotate(
            n=Count(
                "copies__loans",
                filter=Q(
                    copies__loans__borrowed_at__date__gte=date_from, copies__loans__borrowed_at__date__lte=date_to
                ),
            )
        )
        .filter(n__gt=0)
        .select_related("category")
        .order_by("-n", "title")[:limit]
    )
    table = Table("Popular books", ["Rank", "Book", "Author(s)", "Category", "Times borrowed"])
    for rank, book in enumerate(books, start=1):
        table.rows.append([rank, book.title, book.author_names, book.category.name if book.category_id else "", book.n])
    table.totals = [("Period", f"{date_from:%d %b %Y} – {date_to:%d %b %Y}")]
    return table


def inventory() -> Table:
    copies = BookCopy.objects.exclude(status=CopyStatus.WITHDRAWN)
    counts = dict(copies.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    table = Table("Inventory report", ["Book", "Copy", "Barcode", "Shelf", "Status", "Condition"])
    attention = (
        BookCopy.objects.filter(
            Q(status__in=[CopyStatus.LOST, CopyStatus.MAINTENANCE, CopyStatus.DAMAGED])
            | Q(condition__in=[CopyCondition.DAMAGED, CopyCondition.WORN])
        )
        .select_related("book")
        .order_by("status", "book__title")[:2000]
    )
    for copy in attention:
        table.rows.append(
            [
                copy.book.title,
                copy.copy_code,
                copy.barcode,
                copy.shelf_location,
                copy.get_status_display(),
                copy.get_condition_display(),
            ]
        )
    table.totals = [
        (label, counts.get(value, 0)) for value, label in CopyStatus.choices if value != CopyStatus.WITHDRAWN
    ]
    last = InventorySession.objects.filter(status=InventorySession.Status.COMPLETED).first()
    if last:
        table.totals.append(("Missing at last inventory", last.summary.get("missing", 0)))
    table.note = "Listed below: copies that are lost, damaged, worn or in maintenance."
    return table


def fees(date_from, date_to) -> Table:
    date_from, date_to = default_range(date_from, date_to)
    generated = Fee.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to)
    paid = Payment.objects.filter(paid_on__gte=date_from, paid_on__lte=date_to)
    waived = generated.filter(status=FeeStatus.WAIVED)
    table = Table(
        "Fee report",
        ["Date", "Member", "Member ID", "Type", "Book", "Amount", "Paid", "Status"],
        money_columns={5, 6},
    )
    for fee in generated.select_related("member__user", "loan__copy__book").order_by("-created_at")[:5000]:
        table.rows.append(
            [
                timezone.localdate(fee.created_at),
                fee.member.user.full_name,
                fee.member.member_id,
                fee.get_kind_display(),
                fee.book.title if fee.loan_id else "",
                fee.amount,
                fee.amount_paid,
                fee.get_status_display(),
            ]
        )
    table.totals = [
        ("Generated", generated.aggregate(s=Sum("amount"))["s"] or Decimal("0")),
        ("Paid", paid.aggregate(s=Sum("amount"))["s"] or Decimal("0")),
        ("Waived", waived.aggregate(s=Sum("amount"))["s"] or Decimal("0")),
        ("Outstanding (all time)", outstanding_total()),
    ]
    return table


# ---------------------------------------------------------------------------
# Dashboard figures & insights (always computed from real data)
# ---------------------------------------------------------------------------


def library_metrics(today: date | None = None) -> dict:
    today = today or timezone.localdate()
    copies = BookCopy.objects.exclude(status=CopyStatus.WITHDRAWN).filter(book__is_archived=False)
    by_status = dict(copies.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    total = sum(by_status.values())
    in_collection = total - by_status.get(CopyStatus.LOST, 0)
    available = by_status.get(CopyStatus.AVAILABLE, 0)
    overdue_loans = Loan.objects.overdue(today)
    return {
        "titles": Book.objects.filter(is_archived=False).count(),
        "copies": total,
        "available": available,
        "borrowed": by_status.get(CopyStatus.BORROWED, 0),
        "on_hold": by_status.get(CopyStatus.RESERVED, 0),
        "overdue": overdue_loans.count(),
        "overdue_members": overdue_loans.values("member").distinct().count(),
        "reservations": Reservation.objects.filter(status__in=OPEN_RESERVATION_STATUSES).count(),
        "outstanding": outstanding_total(),
        "pulse": round(100 * available / in_collection) if in_collection else 0,
    }


def today_activity(today: date | None = None) -> dict:
    today = today or timezone.localdate()
    return {
        "borrowed": Loan.objects.filter(borrowed_at__date=today).count(),
        "returned": Loan.objects.filter(returned_at__date=today, status=LoanStatus.RETURNED).count(),
        "reservations": Reservation.objects.filter(created_at__date=today).count(),
        "due_today": Loan.objects.active().filter(due_date=today).count(),
    }


def insights(today: date | None = None) -> list[dict]:
    today = today or timezone.localdate()
    items = []
    month_start = today.replace(day=1)
    days_in = (today - month_start).days + 1
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    prev_same_end = min(prev_start + timedelta(days=days_in - 1), prev_end)

    def by_category(start, end):
        return dict(
            Loan.objects.filter(
                borrowed_at__date__gte=start, borrowed_at__date__lte=end, copy__book__category__isnull=False
            )
            .values_list("copy__book__category__name")
            .annotate(n=Count("id"))
            .values_list("copy__book__category__name", "n")
        )

    current, previous = by_category(month_start, today), by_category(prev_start, prev_same_end)
    best = None
    for name, now_count in current.items():
        before = previous.get(name, 0)
        if before >= 3 and now_count != before:
            change = round(100 * (now_count - before) / before)
            if best is None or abs(change) > abs(best[1]):
                best = (name, change)
    if best and abs(best[1]) >= 10:
        direction = "increased" if best[1] > 0 else "decreased"
        items.append({"icon": "trend", "text": f"{best[0]} borrowing {direction} {abs(best[1])}% this month."})

    overdue_count = Loan.objects.overdue(today).count()
    if overdue_count:
        items.append(
            {
                "icon": "alert",
                "text": f"{overdue_count} book{'s are' if overdue_count != 1 else ' is'} currently overdue.",
            }
        )

    top = (
        Loan.objects.filter(borrowed_at__date__gte=month_start)
        .values("copy__book__title")
        .annotate(n=Count("id"))
        .order_by("-n", "copy__book__title")
        .first()
    )
    if top and top["n"] >= 2:
        items.append({"icon": "flame", "text": f"The most borrowed book this month is {top['copy__book__title']}."})

    ready = Reservation.objects.filter(status="ready", picked_at__isnull=True).count()
    if ready:
        items.append(
            {
                "icon": "bookmark",
                "text": f"{ready} reserved book{'s need' if ready != 1 else ' needs'} to be pulled from the shelves.",
            }
        )
    return items
