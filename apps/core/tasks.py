"""Periodic housekeeping, run by `python manage.py run_scheduled_tasks`
(cron / systemd timer / the docker-compose scheduler service — hourly is ideal)."""

import logging

from django.utils import timezone

logger = logging.getLogger("apps.tasks")


def run_all(now=None) -> dict:
    from apps.accounts import ratelimit
    from apps.circulation import reservations
    from apps.notifications import email, reminders

    now = now or timezone.now()
    today = timezone.localdate(now)
    results = {}
    steps = [
        ("reservations", lambda: reservations.expire_reservations(now)),
        ("expiring_holds", lambda: reservations.notify_expiring_holds(now)),
        ("due_soon_reminders", lambda: reminders.send_due_soon(today)),
        ("overdue_reminders", lambda: reminders.send_overdue(today)),
        ("email_retries", email.retry_failed),
        ("rate_limit_cleanup", ratelimit.cleanup),
    ]
    for name, step in steps:
        try:
            results[name] = step()
        except Exception:  # noqa: BLE001 — one failing step must not stop the others
            logger.exception("Scheduled task step %s failed", name)
            results[name] = "failed"
    logger.info("Scheduled tasks finished: %s", results)
    return results
