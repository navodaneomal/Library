"""Email service abstraction.

Emails are rendered from templates (`templates/emails/<name>.{subject.txt,txt,html}`),
logged in EmailLog, and delivered *after* the database transaction commits
through whichever Django email backend is configured (any SMTP provider,
console for development, or a provider-specific backend such as
django-anymail). Failures never break the user's action: they are logged
and retried by the scheduled task.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.config import library_settings

from .models import EmailLog, EmailStatus

logger = logging.getLogger(__name__)


def base_context() -> dict:
    s = library_settings()
    return {
        "library": s,
        "library_name": s.library_name,
        "site_url": settings.SITE_URL,
        "currency": s.currency,
    }


def render(template: str, context: dict) -> tuple[str, str, str]:
    ctx = {**base_context(), **context}
    subject = " ".join(render_to_string(f"emails/{template}.subject.txt", ctx).split())
    text = render_to_string(f"emails/{template}.txt", ctx)
    html = render_to_string(f"emails/{template}.html", ctx)
    return subject, text, html


REDACTED = "(Not stored: this email contained a private sign-in link.)"


def send_templated(
    *,
    template: str,
    to: str | None,
    context: dict,
    user=None,
    dedupe_key: str | None = None,
    sensitive: bool = False,
) -> EmailLog | None:
    """Queue an email; returns None when there is no address or it was already sent.

    `sensitive` emails (password / verification links) are delivered from
    memory and only a redacted copy is logged, so links never sit in the DB.
    """
    if not to:
        return None
    if dedupe_key and EmailLog.objects.filter(dedupe_key=dedupe_key).exists():
        return None
    subject, text, html = render(template, context)
    try:
        with transaction.atomic():
            log = EmailLog.objects.create(
                to_email=to,
                user=user,
                template=template,
                subject=subject[:255],
                body_text=REDACTED if sensitive else text,
                body_html="" if sensitive else html,
                dedupe_key=dedupe_key,
            )
    except IntegrityError:
        return None  # a concurrent run already queued it
    if sensitive:
        log.attempts = settings.EMAIL_MAX_ATTEMPTS - 1  # never retried from the redacted copy
        log.save(update_fields=["attempts"])
        transaction.on_commit(lambda: deliver(log.pk, content=(text, html)))
    else:
        transaction.on_commit(lambda: deliver(log.pk))
    return log


def deliver(log_id: int, content: tuple[str, str] | None = None) -> bool:
    log = EmailLog.objects.filter(pk=log_id).first()
    if log is None or log.status == EmailStatus.SENT:
        return True
    if content is None and log.body_text == REDACTED:
        return False
    text, html = content if content is not None else (log.body_text, log.body_html)
    reply_to = [library_settings().library_email] if library_settings().library_email else None
    message = EmailMultiAlternatives(
        subject=log.subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[log.to_email],
        reply_to=reply_to,
    )
    if html:
        message.attach_alternative(html, "text/html")
    log.attempts += 1
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — any transport error must not escape
        logger.warning("Email %s to %s failed: %s", log.template, log.to_email, exc)
        log.status = EmailStatus.FAILED
        log.last_error = str(exc)[:2000]
        log.save(update_fields=["status", "attempts", "last_error"])
        return False
    log.status = EmailStatus.SENT
    log.sent_at = timezone.now()
    log.last_error = ""
    log.save(update_fields=["status", "attempts", "sent_at", "last_error"])
    return True


def retry_failed() -> int:
    sent = 0
    for log_id in (
        EmailLog.objects.exclude(body_text=REDACTED)
        .filter(
            status__in=[EmailStatus.FAILED, EmailStatus.QUEUED],
            attempts__lt=settings.EMAIL_MAX_ATTEMPTS,
            created_at__lt=timezone.now() - timedelta(minutes=2),
        )
        .values_list("pk", flat=True)[:200]
    ):
        sent += int(deliver(log_id))
    return sent


def send_test_email(to: str) -> tuple[bool, str]:
    subject, text, html = render("test", {})
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    message.attach_alternative(html, "text/html")
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Test email failed: %s", exc)
        return False, str(exc)
    return True, ""
