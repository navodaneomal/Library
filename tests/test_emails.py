from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.circulation.services import borrow, return_copy
from apps.core.tasks import run_all
from apps.fees.services import record_payment
from apps.notifications import email as email_service
from apps.notifications import reminders
from apps.notifications.models import EmailLog, Notification

from .factories import LibraryTestCase, at, book, member, settings


class ReminderTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.member = member(name="Navoda Perera")
        self.book = book("The Hobbit", copies=1)

    def test_due_soon_reminder_three_days_before_sent_once(self):
        borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(11))  # due in 3 days
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(reminders.send_due_soon(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            reminders.send_due_soon()  # idempotent
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("due soon", message.subject)
        self.assertIn("The Hobbit", message.body)
        self.assertIn("avoid late fees", message.body)
        self.assertEqual(Notification.objects.filter(kind="due_soon").count(), 1)

    def test_not_reminded_too_early(self):
        borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(5))  # due in 9 days
        self.assertEqual(reminders.send_due_soon(), 0)

    def test_overdue_reminder_includes_estimated_fee_and_warning(self):
        borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(17))  # 3 days overdue
        with self.captureOnCommitCallbacks(execute=True):
            reminders.send_overdue()
        message = mail.outbox[0]
        self.assertIn("overdue", message.subject)
        self.assertIn("3 days", message.body)
        self.assertIn("150.00", message.body)
        self.assertIn("continue increasing", message.body)

    def test_overdue_reminders_repeat_on_interval(self):
        loan = borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(16))
        today = timezone.localdate()
        with self.captureOnCommitCallbacks(execute=True):
            reminders.send_overdue(today)
            reminders.send_overdue(today + timedelta(days=3))  # same round → nothing new
            reminders.send_overdue(today + timedelta(days=7))  # next round
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(loan)

    def test_member_can_opt_out_of_reminder_emails(self):
        self.member.email_reminders = False
        self.member.save()
        borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(11))
        with self.captureOnCommitCallbacks(execute=True):
            reminders.send_due_soon()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.filter(kind="due_soon").count(), 1)  # still in-app

    def test_reminders_can_be_switched_off(self):
        settings(reminders_enabled=False)
        borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(17))
        self.assertEqual(reminders.send_overdue(), 0)

    def test_members_without_email_get_in_app_only(self):
        child = member(email="")
        self.assertIsNone(child.user.email)
        borrow(child, self.book.copies.first(), staff=self.librarian, now=at(17))
        with self.captureOnCommitCallbacks(execute=True):
            reminders.send_overdue()
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(Notification.objects.filter(user=child.user, kind="overdue").exists())


class TransactionalEmailTests(LibraryTestCase):
    def test_fee_and_payment_emails(self):
        m = member()
        copy = book(copies=1).copies.first()
        borrow(m, copy, staff=self.librarian, now=at(16))
        with self.captureOnCommitCallbacks(execute=True):
            result = return_copy(copy, staff=self.librarian)
        self.assertIn("fee", mail.outbox[-1].subject.lower())
        self.assertIn("100.00", mail.outbox[-1].body)
        with self.captureOnCommitCallbacks(execute=True):
            record_payment(result.fee, amount=Decimal("100"), method="cash", reference="R123", staff=self.librarian)
        self.assertIn("Payment received", mail.outbox[-1].subject)
        result.fee.refresh_from_db()
        self.assertEqual(result.fee.status, "paid")

    def test_partial_payment_keeps_fee_pending(self):
        from apps.fees.models import Fee
        from apps.fees.services import FeeError

        fee = Fee.objects.create(member=member(), amount=Decimal("300"), description="Late")
        record_payment(fee, amount=Decimal("100"), staff=self.librarian)
        fee.refresh_from_db()
        self.assertEqual((fee.status, fee.outstanding), ("pending", Decimal("200.00")))
        with self.assertRaises(FeeError):
            record_payment(fee, amount=Decimal("500"), staff=self.librarian)

    def test_failed_delivery_is_logged_not_raised_and_retried(self):
        m = member()
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP down")):
            with self.captureOnCommitCallbacks(execute=True):
                email_service.send_templated(template="test", to=m.user.email, context={})
        log = EmailLog.objects.get()
        self.assertEqual((log.status, log.attempts), ("failed", 1))
        self.assertIn("SMTP down", log.last_error)
        EmailLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(email_service.retry_failed(), 1)
        log.refresh_from_db()
        self.assertEqual(log.status, "sent")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_scheduled_tasks_run_all_steps(self):
        results = run_all()
        self.assertEqual(
            set(results),
            {
                "reservations",
                "expiring_holds",
                "due_soon_reminders",
                "overdue_reminders",
                "email_retries",
                "rate_limit_cleanup",
            },
        )
        self.assertNotIn("failed", results.values())

    def test_email_templates_render_without_escaping_text(self):
        m = member(name="O'Brien")
        subject, text, html = email_service.render("welcome", {"member": m, "card_url": "http://x"})
        self.assertIn("O'Brien", text)
        self.assertIn("O&#x27;Brien", html)
