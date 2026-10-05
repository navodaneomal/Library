import logging

from .http import client_ip
from .models import AuditLog

logger = logging.getLogger("apps.audit")


def record(actor, action: str, summary: str, *, target=None, changes: dict | None = None, request=None) -> AuditLog:
    """Write an audit entry. `target` may be any model instance."""
    entry = AuditLog(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_name=(getattr(actor, "full_name", "") or getattr(actor, "email", "") or "System")[:150],
        action=action,
        summary=summary[:255],
        changes=changes or {},
        ip_address=client_ip(request) if request is not None else None,
    )
    if target is not None:
        entry.target_type = target._meta.label_lower
        entry.target_id = str(target.pk)
        entry.target_label = str(target)[:255]
    entry.save()
    logger.info("audit %s by %s: %s", action, entry.actor_name, summary)
    return entry


def diff(before: dict, after: dict) -> dict:
    """Return {field: [old, new]} for values that changed (as strings for JSON)."""
    changes = {}
    for key, new in after.items():
        old = before.get(key)
        if old != new:
            changes[key] = [None if old is None else str(old), None if new is None else str(new)]
    return changes
