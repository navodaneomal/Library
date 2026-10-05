"""Template helpers shared across the public site, member portal and staff area."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.safestring import mark_safe

register = template.Library()


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


@register.filter
def money(value, currency: str | None = None) -> str:
    """1250 → 'LKR 1,250.00' using the library's configured currency."""
    if value in (None, ""):
        value = 0
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if currency is None:
        from apps.core.config import library_settings

        currency = library_settings().currency
    sign = "-" if amount < 0 else ""
    return f"{sign}{currency} {abs(amount):,.2f}"


# ---------------------------------------------------------------------------
# Dates — "Smart Due Countdown"
# ---------------------------------------------------------------------------


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return timezone.localdate(value)
    return value if isinstance(value, date) else None


@register.filter
def due_countdown(value, today=None) -> str:
    due = _as_date(value)
    if due is None:
        return ""
    today = today or timezone.localdate()
    days = (due - today).days
    if days > 1:
        return f"{days} days left"
    if days == 1:
        return "Due tomorrow"
    if days == 0:
        return "Due today"
    overdue = -days
    return f"{overdue} day{'s' if overdue != 1 else ''} overdue"


@register.filter
def due_state(value, today=None) -> str:
    due = _as_date(value)
    if due is None:
        return "neutral"
    days = (due - (today or timezone.localdate())).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days <= 3:
        return "soon"
    return "ok"


@register.filter
def nice_date(value, with_year: bool = True) -> str:
    """'19 October 2026' (no leading zero)."""
    day = _as_date(value)
    if day is None:
        return ""
    return f"{day.day} {day:%B %Y}" if with_year else f"{day.day} {day:%B}"


@register.filter
def short_date(value) -> str:
    """'19 Oct 2026'."""
    day = _as_date(value)
    return f"{day.day} {day:%b %Y}" if day else ""


@register.filter
def ago(value) -> str:
    """'3 days ago' — a single unit, calmer than '2 days, 23 hours ago'."""
    from django.utils.timesince import timesince

    if not value:
        return ""
    text = timesince(value, depth=1)
    return "just now" if text.startswith("0\xa0minutes") else f"{text} ago"


@register.simple_tag
def greeting(name: str = "") -> str:
    hour = timezone.localtime().hour
    part = "morning" if 4 <= hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part}, {name}." if name else f"Good {part}."


# ---------------------------------------------------------------------------
# Status presentation (never colour alone: tone + text label)
# ---------------------------------------------------------------------------

TONES = {
    # availability / copy status
    "available": "success",
    "borrowed": "danger",
    "reserved": "warning",
    "unavailable": "neutral",
    "maintenance": "neutral",
    "damaged": "danger",
    "lost": "danger",
    "withdrawn": "neutral",
    # reservation
    "waiting": "info",
    "ready": "success",
    "collected": "neutral",
    "expired": "neutral",
    "cancelled": "neutral",
    # loan
    "active": "info",
    "returned": "neutral",
    "overdue": "danger",
    # fee
    "pending": "warning",
    "paid": "success",
    "waived": "neutral",
    # member
    "suspended": "danger",
    "inactive": "neutral",
    # email
    "sent": "success",
    "queued": "info",
    "failed": "danger",
    # inventory
    "ok": "success",
    "unexpected_location": "warning",
    "duplicate": "info",
    "unknown": "danger",
    "status_mismatch": "warning",
    "open": "info",
    "completed": "success",
}


@register.filter
def tone(status: str) -> str:
    return TONES.get(str(status), "neutral")


@register.simple_tag
def badge(status: str, label: str = "") -> str:
    return format_html(
        '<span class="badge badge--{}"><span class="badge__dot" aria-hidden="true"></span>{}</span>',
        tone(status),
        label or str(status).replace("_", " ").capitalize(),
    )


# ---------------------------------------------------------------------------
# Catalogue helpers
# ---------------------------------------------------------------------------


@register.simple_tag
def availability_of(book):
    from apps.catalogue.availability import for_book

    return for_book(book)


@register.filter
def cover_tone(book) -> int:
    from apps.catalogue.covers import placeholder_tone

    return placeholder_tone(getattr(book, "title", str(book)))


@register.filter
def shelf_steps(code: str) -> list[str]:
    from apps.catalogue.shelves import describe

    return describe(code)


@register.simple_tag
def barcode(code: str, height: int = 56, module_width: float = 2.0):
    from apps.barcodes.code128 import BarcodeError, svg

    try:
        return mark_safe(svg(str(code), height=height, module_width=module_width))  # noqa: S308 — escaped inside svg()
    except BarcodeError:
        return format_html('<span class="muted">{}</span>', code)


@register.filter
def width_class(score) -> str:
    """Map 0–10 or 0–100 to a .w-NN utility class (no inline styles — strict CSP)."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "w-0"
    if value <= 10:
        value *= 10
    return f"w-{int(max(0, min(100, round(value / 5) * 5)))}"


@register.filter
def initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


@register.filter
def pluralize_n(count, word: str) -> str:
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 0
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


@register.simple_tag(takes_context=True)
def query(context, **changes) -> str:
    """Current query string with some parameters changed or removed (None)."""
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in changes.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    if hasattr(params, "urlencode"):
        encoded = params.urlencode()
    else:
        encoded = urlencode(params)
    return f"?{encoded}" if encoded else "?"


@register.filter
def is_money(value) -> bool:
    return isinstance(value, Decimal)


@register.filter
def get_index(sequence, index):
    try:
        return sequence[int(index)]
    except (IndexError, TypeError, ValueError):
        return ""


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag
def icon(name: str, css: str = ""):
    from django.templatetags.static import static

    return format_html(
        '<svg class="icon {}" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>',
        css,
        static("img/icons.svg"),
        name,
    )
