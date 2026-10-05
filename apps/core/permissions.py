"""Role-based access control. Every protected view checks a capability on the
server; templates only use the same table to decide which buttons to show."""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import Role

A, L, AD = Role.ASSISTANT, Role.LIBRARIAN, Role.ADMIN

CAPABILITIES: dict[str, frozenset] = {
    "staff.access": frozenset({A, L, AD}),
    "circulation": frozenset({A, L, AD}),
    "reservations.manage": frozenset({A, L, AD}),
    "members.view": frozenset({A, L, AD}),
    "members.manage": frozenset({L, AD}),
    "books.view": frozenset({A, L, AD}),
    "books.manage": frozenset({L, AD}),
    "fees.view": frozenset({A, L, AD}),
    "fees.manage": frozenset({L, AD}),
    "overdue.view": frozenset({A, L, AD}),
    "reminders.send": frozenset({L, AD}),
    "inventory": frozenset({L, AD}),
    "reports": frozenset({L, AD}),
    "announcements": frozenset({L, AD}),
    "settings": frozenset({AD}),
    "staff.manage": frozenset({AD}),
    "audit.view": frozenset({AD}),
    "backup": frozenset({AD}),
}


def can(user, capability: str) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return user.role in CAPABILITIES[capability]


def capabilities_for(user) -> frozenset[str]:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    return frozenset(cap for cap, roles in CAPABILITIES.items() if user.role in roles)


def require_capability(capability: str):
    """Anonymous visitors are sent to the staff sign-in page; signed-in users
    without the capability get a plain 'Access denied.' (HTTP 403)."""

    if capability not in CAPABILITIES:  # fail loudly on typos at import time
        raise KeyError(capability)

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), reverse("staff:login"))
            if not can(request.user, capability):
                raise PermissionDenied("Access denied.")
            return view(request, *args, **kwargs)

        wrapped.required_capability = capability
        return wrapped

    return decorator


def member_required(view):
    """Signed-in members only (staff accounts may also hold a member profile)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("accounts:login"))
        if not hasattr(request.user, "member"):
            if request.user.is_staff_member:
                from django.shortcuts import redirect

                return redirect("staff:dashboard")
            raise PermissionDenied("Access denied.")
        return view(request, *args, **kwargs)

    return wrapped
