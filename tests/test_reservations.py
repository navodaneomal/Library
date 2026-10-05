from datetime import timedelta

from django.core import mail
from django.utils import timezone

from apps.catalogue.models import CopyStatus
from apps.circulation import reservations as rs
from apps.circulation.models import ReservationStatus
from apps.circulation.services import borrow, change_copy_state, return_copy
from apps.members.models import MemberStatus

from .factories import LibraryTestCase, book, member, settings


class ReservationTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.book = book("The Hobbit", copies=1)
        self.copy = self.book.copies.first()
        self.a, self.b, self.c = member("Member A"), member("Member B"), member("Member C")

    def lend_out(self):
        borrow(member("Borrower"), self.copy, staff=self.librarian)

    def test_reserving_an_available_book_holds_a_copy(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = rs.reserve(self.a, self.book)
        self.copy.refresh_from_db()
        self.assertEqual(res.status, ReservationStatus.READY)
        self.assertEqual(res.copy, self.copy)
        self.assertEqual(self.copy.status, CopyStatus.RESERVED)
        self.assertEqual(res.collect_by, timezone.localdate() + timedelta(days=3))
        self.assertTrue(any("waiting for you" in m.subject for m in mail.outbox))

    def test_unavailable_book_joins_queue_fifo(self):
        self.lend_out()
        ra, rb, rc = rs.reserve(self.a, self.book), rs.reserve(self.b, self.book), rs.reserve(self.c, self.book)
        self.assertEqual([rs.queue_position(r) for r in (ra, rb, rc)], [1, 2, 3])
        self.assertTrue(all(r.status == ReservationStatus.WAITING for r in (ra, rb, rc)))

    def test_duplicate_reservation_is_refused(self):
        self.lend_out()
        rs.reserve(self.a, self.book)
        with self.assertRaisesMessage(rs.ReservationError, "already have an active reservation"):
            rs.reserve(self.a, self.book)

    def test_returned_copy_goes_to_first_in_queue_and_notifies(self):
        self.lend_out()
        ra, rb = rs.reserve(self.a, self.book), rs.reserve(self.b, self.book)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            result = return_copy(self.copy, staff=self.librarian)
        ra.refresh_from_db()
        rb.refresh_from_db()
        self.assertEqual(result.hold, ra)
        self.assertEqual(ra.status, ReservationStatus.READY)
        self.assertIsNotNone(ra.picked_at)  # straight onto the hold shelf
        self.assertEqual(rb.status, ReservationStatus.WAITING)
        self.assertEqual(rs.queue_position(rb), 1)
        self.assertEqual([m.to for m in mail.outbox], [[self.a.user.email]])
        self.assertTrue(self.a.user.notifications.filter(kind="reservation_ready").exists())

    def test_expired_hold_moves_to_next_member(self):
        self.lend_out()
        ra, rb = rs.reserve(self.a, self.book), rs.reserve(self.b, self.book)
        return_copy(self.copy, staff=self.librarian)
        later = timezone.now() + timedelta(days=5)
        outcome = rs.expire_reservations(now=later)
        ra.refresh_from_db()
        rb.refresh_from_db()
        self.assertEqual(outcome["expired_holds"], 1)
        self.assertEqual(ra.status, ReservationStatus.EXPIRED)
        self.assertEqual(rb.status, ReservationStatus.READY)
        self.assertEqual(rb.copy, self.copy)

    def test_expired_hold_with_empty_queue_returns_copy_to_shelf(self):
        ra = rs.reserve(self.a, self.book)
        rs.expire_reservations(now=timezone.now() + timedelta(days=10))
        ra.refresh_from_db()
        self.copy.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.EXPIRED)
        self.assertEqual(self.copy.status, CopyStatus.AVAILABLE)

    def test_waiting_reservations_can_expire_when_configured(self):
        settings(reservation_waiting_expiry_days=30)
        self.lend_out()
        ra = rs.reserve(self.a, self.book)
        rs.expire_reservations(now=timezone.now() + timedelta(days=40))
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.EXPIRED)

    def test_collecting_completes_reservation(self):
        ra = rs.reserve(self.a, self.book)
        loan = borrow(self.a, self.copy, staff=self.librarian)
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.COLLECTED)
        self.assertEqual(ra.loan, loan)

    def test_held_copy_cannot_go_to_someone_else(self):
        rs.reserve(self.a, self.book)
        self.copy.refresh_from_db()
        from apps.circulation.rules import check_borrow

        self.assertIn("on hold for another member", check_borrow(self.b, self.copy).first_problem)

    def test_taking_another_copy_releases_the_held_one(self):
        second = self.book.copies.create(copy_number=2, copy_code="X-2", barcode="X2", status=CopyStatus.AVAILABLE)
        ra = rs.reserve(self.a, self.book)
        held = ra.copy
        other = second if held.pk != second.pk else self.copy
        borrow(self.a, other, staff=self.librarian)
        held.refresh_from_db()
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.COLLECTED)
        self.assertEqual(held.status, CopyStatus.AVAILABLE)

    def test_cancel_ready_hold_passes_copy_on(self):
        self.lend_out()
        ra, rb = rs.reserve(self.a, self.book), rs.reserve(self.b, self.book)
        return_copy(self.copy, staff=self.librarian)
        ra.refresh_from_db()
        rs.cancel(ra, actor=self.a.user)
        rb.refresh_from_db()
        self.assertEqual(rb.status, ReservationStatus.READY)

    def test_suspended_member_is_skipped_but_keeps_place(self):
        self.lend_out()
        ra, rb = rs.reserve(self.a, self.book), rs.reserve(self.b, self.book)
        self.a.status = MemberStatus.SUSPENDED
        self.a.save()
        return_copy(self.copy, staff=self.librarian)
        ra.refresh_from_db()
        rb.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.WAITING)
        self.assertEqual(rb.status, ReservationStatus.READY)

    def test_missing_held_copy_puts_member_back_at_front(self):
        ra = rs.reserve(self.a, self.book)
        change_copy_state(ra.copy, status=CopyStatus.LOST, condition="lost", actor=self.librarian)
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.WAITING)
        self.assertEqual(rs.queue_position(ra), 1)

    def test_repaired_copy_is_offered_to_queue(self):
        change_copy_state(self.copy, status=CopyStatus.MAINTENANCE, condition="good", actor=self.librarian)
        ra = rs.reserve(self.a, self.book)
        self.assertEqual(ra.status, ReservationStatus.WAITING)
        change_copy_state(self.copy, status=CopyStatus.AVAILABLE, condition="good", actor=self.librarian)
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.READY)

    def test_new_copy_is_offered_to_queue(self):
        from apps.catalogue.services import add_copy

        self.lend_out()
        ra = rs.reserve(self.a, self.book)
        add_copy(self.book, barcode="NEWCOPY1")
        ra.refresh_from_db()
        self.assertEqual(ra.status, ReservationStatus.READY)

    def test_maximum_reservations(self):
        settings(max_reservations_per_member=1)
        self.lend_out()
        rs.reserve(self.a, self.book)
        with self.assertRaisesMessage(rs.ReservationError, "at most 1"):
            rs.reserve(self.a, book(copies=1))

    def test_estimate_uses_due_dates(self):
        self.lend_out()
        ra = rs.reserve(self.a, self.book)
        estimate = rs.estimated_availability(ra)
        self.assertEqual(estimate.date, timezone.localdate() + timedelta(days=14))

    def test_expiring_reminder_sent_once(self):
        ra = rs.reserve(self.a, self.book)
        soon = ra.expires_at - timedelta(hours=2)
        self.assertEqual(rs.notify_expiring_holds(now=soon), 1)
        self.assertEqual(rs.notify_expiring_holds(now=soon), 0)
