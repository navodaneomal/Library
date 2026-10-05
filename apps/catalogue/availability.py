"""Public availability states for a title, derived from its copies.

Members only ever see aggregate states — never who has a book.
"""

from dataclasses import dataclass

from django.db.models import Count, Q

from .models import UNUSABLE_CONDITIONS, CopyStatus


class State:
    AVAILABLE = "available"
    RESERVED = "reserved"
    BORROWED = "borrowed"
    UNAVAILABLE = "unavailable"


LABELS = {
    State.AVAILABLE: "Available",
    State.RESERVED: "Reserved",
    State.BORROWED: "Borrowed",
    State.UNAVAILABLE: "Unavailable",
}

HINTS = {
    State.AVAILABLE: "Ready to borrow",
    State.RESERVED: "Copies are on hold for other members",
    State.BORROWED: "All copies are on loan",
    State.UNAVAILABLE: "Temporarily unavailable",
}


@dataclass(frozen=True)
class Availability:
    state: str
    total: int
    available: int
    on_hold: int
    borrowed: int

    @property
    def label(self) -> str:
        return LABELS[self.state]

    @property
    def hint(self) -> str:
        return HINTS[self.state]

    @property
    def is_available(self) -> bool:
        return self.state == State.AVAILABLE


def _usable(prefix: str = "copies__") -> Q:
    return ~Q(**{f"{prefix}condition__in": UNUSABLE_CONDITIONS})


def annotate_availability(queryset):
    """Add copy counts to a Book queryset in a single query."""
    return queryset.annotate(
        copies_total=Count("copies", filter=~Q(copies__status=CopyStatus.WITHDRAWN), distinct=True),
        copies_available=Count("copies", filter=Q(copies__status=CopyStatus.AVAILABLE) & _usable(), distinct=True),
        copies_on_hold=Count("copies", filter=Q(copies__status=CopyStatus.RESERVED), distinct=True),
        copies_borrowed=Count("copies", filter=Q(copies__status=CopyStatus.BORROWED), distinct=True),
    )


def from_counts(total: int, available: int, on_hold: int, borrowed: int) -> Availability:
    if available > 0:
        state = State.AVAILABLE
    elif on_hold > 0:
        state = State.RESERVED
    elif borrowed > 0:
        state = State.BORROWED
    else:
        state = State.UNAVAILABLE
    return Availability(state, total, available, on_hold, borrowed)


def for_book(book) -> Availability:
    """Use annotated counts when present, otherwise query."""
    if hasattr(book, "copies_available"):
        return from_counts(book.copies_total, book.copies_available, book.copies_on_hold, book.copies_borrowed)
    copies = list(book.copies.exclude(status=CopyStatus.WITHDRAWN).values_list("status", "condition"))
    total = len(copies)
    available = sum(1 for s, c in copies if s == CopyStatus.AVAILABLE and c not in UNUSABLE_CONDITIONS)
    on_hold = sum(1 for s, _ in copies if s == CopyStatus.RESERVED)
    borrowed = sum(1 for s, _ in copies if s == CopyStatus.BORROWED)
    return from_counts(total, available, on_hold, borrowed)
