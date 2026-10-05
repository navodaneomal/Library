"""What members are told, and when. Each function creates an in-app
notification and (where the member has an email address) queues an email."""

from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.templatetags.library import money

from .email import send_templated
from .models import Notification, NotificationKind


def absolute(path: str) -> str:
    return f"{settings.SITE_URL}{path}"


def create(user, kind: str, title: str, body: str = "", url: str = "", dedupe_key: str | None = None):
    if dedupe_key and Notification.objects.filter(dedupe_key=dedupe_key).exists():
        return None
    try:
        with transaction.atomic():
            return Notification.objects.create(
                user=user, kind=kind, title=title[:160], body=body, url=url, dedupe_key=dedupe_key
            )
    except IntegrityError:
        return None


def welcome(member):
    create(
        member.user,
        NotificationKind.ACCOUNT,
        "Welcome to the library",
        f"Your member ID is {member.member_id}. Your digital library card is ready.",
        reverse("portal:card"),
    )
    send_templated(
        template="welcome",
        to=member.user.email,
        user=member.user,
        context={"member": member, "card_url": absolute(reverse("portal:card"))},
    )


def verification(user, link: str):
    send_templated(
        template="verify_email", to=user.email, user=user, context={"user": user, "link": link}, sensitive=True
    )


def set_password_invite(user, link: str):
    send_templated(
        template="set_password", to=user.email, user=user, context={"user": user, "link": link}, sensitive=True
    )


def reservation_ready(reservation):
    member, book = reservation.member, reservation.book
    url = reverse("portal:reservations")
    collect_by = reservation.collect_by
    create(
        member.user,
        NotificationKind.RESERVATION_READY,
        f"{book.title} is waiting for you",
        f"Please collect it before {collect_by.day} {collect_by:%B}." if collect_by else "",
        url,
    )
    send_templated(
        template="reservation_ready",
        to=member.user.email,
        user=member.user,
        context={
            "member": member,
            "book": book,
            "reservation": reservation,
            "collect_by": collect_by,
            "url": absolute(url),
        },
    )


def reservation_expiring(reservation):
    member, book = reservation.member, reservation.book
    url = reverse("portal:reservations")
    collect_by = reservation.collect_by
    create(
        member.user,
        NotificationKind.RESERVATION_EXPIRING,
        f"Last day to collect {book.title}",
        f"Your hold ends on {collect_by.day} {collect_by:%B}." if collect_by else "",
        url,
        dedupe_key=f"res-expiring:{reservation.pk}:{reservation.ready_at:%Y%m%d%H%M%S}",
    )
    send_templated(
        template="reservation_expiring",
        to=member.user.email,
        user=member.user,
        context={"member": member, "book": book, "collect_by": collect_by, "url": absolute(url)},
        dedupe_key=f"email:res-expiring:{reservation.pk}:{reservation.ready_at:%Y%m%d%H%M%S}",
    )


def reservation_expired(reservation):
    create(
        reservation.member.user,
        NotificationKind.RESERVATION_EXPIRED,
        f"Your reservation for {reservation.book.title} has expired",
        "You can reserve it again at any time.",
        reservation.book.get_absolute_url(),
    )


def due_soon(loan, dedupe_key: str):
    member, book = loan.member, loan.copy.book
    url = reverse("portal:my_books")
    days = (loan.due_date - timezone.localdate()).days
    when = "today" if days <= 0 else "tomorrow" if days == 1 else f"in {days} days"
    create(
        member.user,
        NotificationKind.DUE_SOON,
        f"{book.title} is due {when}",
        f"Due date: {loan.due_date.day} {loan.due_date:%B %Y}.",
        url,
        dedupe_key=dedupe_key,
    )
    if member.email_reminders:
        send_templated(
            template="due_soon",
            to=member.user.email,
            user=member.user,
            context={"member": member, "book": book, "loan": loan, "when": when, "url": absolute(url)},
            dedupe_key=f"email:{dedupe_key}",
        )


def overdue(loan, quote, dedupe_key: str):
    member, book = loan.member, loan.copy.book
    url = reverse("portal:my_books")
    days = quote.days_overdue
    create(
        member.user,
        NotificationKind.OVERDUE,
        f"{book.title} is overdue",
        f"{days} day{'s' if days != 1 else ''} overdue. Current estimated fee: {money(quote.amount)}.",
        url,
        dedupe_key=dedupe_key,
    )
    if member.email_reminders:
        send_templated(
            template="overdue",
            to=member.user.email,
            user=member.user,
            context={"member": member, "book": book, "loan": loan, "quote": quote, "url": absolute(url)},
            dedupe_key=f"email:{dedupe_key}",
        )


def fee_generated(fee):
    member = fee.member
    url = reverse("portal:fees")
    create(member.user, NotificationKind.FEE, f"New fee: {money(fee.amount)}", fee.description, url)
    send_templated(
        template="fee_generated",
        to=member.user.email,
        user=member.user,
        context={"member": member, "fee": fee, "url": absolute(url)},
    )


def payment_received(payment, total: Decimal | None = None):
    member = payment.fee.member
    amount = total if total is not None else payment.amount
    url = reverse("portal:fees")
    create(member.user, NotificationKind.PAYMENT, f"Payment received: {money(amount)}", "Thank you.", url)
    send_templated(
        template="payment_confirmation",
        to=member.user.email,
        user=member.user,
        context={"member": member, "payment": payment, "amount": amount, "url": absolute(url)},
    )


def book_lost(loan, fees):
    member, book = loan.member, loan.copy.book
    url = reverse("portal:fees")
    total = sum((f.amount for f in fees), Decimal("0"))
    create(
        member.user,
        NotificationKind.FEE,
        f"{book.title} was recorded as lost",
        f"Fees of {money(total)} have been added to your account." if fees else "",
        url,
    )
    send_templated(
        template="book_lost",
        to=member.user.email,
        user=member.user,
        context={"member": member, "book": book, "fees": fees, "total": total, "url": absolute(url)},
    )


def announce(announcement, users) -> int:
    rows = [
        Notification(
            user=user,
            kind=NotificationKind.ANNOUNCEMENT,
            title=announcement.title[:160],
            body=announcement.body,
        )
        for user in users
    ]
    Notification.objects.bulk_create(rows, batch_size=500)
    return len(rows)
