from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.catalogue.models import BookCopy, CopyStatus
from apps.circulation.models import Loan, LoanStatus
from apps.circulation.rules import check_borrow, member_standing
from apps.circulation.services import (
    CirculationError,
    borrow,
    change_copy_state,
    mark_lost,
    renew,
    return_copy,
)
from apps.fees.models import Fee, FeeKind, FeeStatus
from apps.members.models import MemberStatus

from .factories import LibraryTestCase, at, book, member, settings


class BorrowRulesTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.member = member()
        self.book = book("The Hobbit", copies=3)
        self.copy = self.book.copies.first()

    def test_ready_to_borrow_calculates_due_date(self):
        check = check_borrow(self.member, self.copy)
        self.assertTrue(check.ok)
        self.assertEqual(check.due_date, timezone.localdate() + timedelta(days=14))

    def test_book_specific_loan_period(self):
        self.book.loan_period_days = 7
        self.book.save()
        self.copy.refresh_from_db()
        self.assertEqual(check_borrow(self.member, self.copy).due_date, timezone.localdate() + timedelta(days=7))

    def test_due_date_follows_configured_loan_period(self):
        settings(loan_period_days=21)
        self.assertEqual(check_borrow(self.member, self.copy).due_date, timezone.localdate() + timedelta(days=21))

    def test_suspended_member_is_blocked_with_reason(self):
        self.member.status = MemberStatus.SUSPENDED
        self.member.status_reason = "Lost card"
        self.member.save()
        check = check_borrow(self.member, self.copy)
        self.assertFalse(check.ok)
        self.assertIn("suspended", check.first_problem)
        self.assertIn("Lost card", check.first_problem)

    def test_overdue_books_block_borrowing_with_count(self):
        other = book(copies=3)
        borrow(self.member, other.copies.all()[0], staff=self.librarian, now=at(20))
        third = book(copies=1)
        borrow(self.member, third.copies.first(), staff=self.librarian, now=at(30))
        check = check_borrow(self.member, self.copy)
        self.assertFalse(check.ok)
        self.assertIn("2 overdue books must be returned first.", [p.message for p in check.blocks])

    def test_overdue_rule_can_be_disabled(self):
        settings(block_on_overdue=False)
        borrow(self.member, book(copies=1).copies.first(), staff=self.librarian, now=at(20))
        self.assertTrue(check_borrow(self.member, self.copy).ok)

    def test_unpaid_fees_over_threshold_block(self):
        Fee.objects.create(member=self.member, amount=Decimal("600"), description="Old fee")
        check = check_borrow(self.member, self.copy)
        self.assertFalse(check.ok)
        self.assertIn("exceed", check.first_problem)

    def test_unpaid_fees_under_threshold_allowed(self):
        Fee.objects.create(member=self.member, amount=Decimal("100"), description="Small fee")
        self.assertTrue(check_borrow(self.member, self.copy).ok)

    def test_maximum_active_loans(self):
        settings(max_active_loans=2)
        for _ in range(2):
            borrow(self.member, book(copies=1).copies.first(), staff=self.librarian)
        check = check_borrow(self.member, self.copy)
        self.assertIn("Borrowing limit reached (2 of 2 books).", [p.message for p in check.blocks])

    def test_copy_already_on_loan(self):
        other = member()
        borrow(other, self.copy, staff=self.librarian)
        self.copy.refresh_from_db()
        check = check_borrow(self.member, self.copy)
        self.assertFalse(check.ok)
        self.assertIn("already on loan", check.first_problem)

    def test_member_cannot_borrow_same_title_twice(self):
        copies = list(self.book.copies.all())
        borrow(self.member, copies[0], staff=self.librarian)
        check = check_borrow(self.member, copies[1])
        self.assertIn("already has a copy of this book", check.first_problem)

    def test_duplicate_titles_allowed_when_policy_allows(self):
        settings(allow_duplicate_title_loans=True)
        copies = list(self.book.copies.all())
        borrow(self.member, copies[0], staff=self.librarian)
        self.assertTrue(check_borrow(self.member, copies[1]).ok)

    def test_damaged_and_lost_copies_cannot_be_borrowed(self):
        change_copy_state(self.copy, status=CopyStatus.AVAILABLE, condition="damaged", actor=self.librarian)
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.status, CopyStatus.DAMAGED)
        self.assertFalse(check_borrow(self.member, self.copy).ok)
        lost = self.book.copies.all()[1]
        change_copy_state(lost, status=CopyStatus.AVAILABLE, condition="lost", actor=self.librarian)
        lost.refresh_from_db()
        self.assertEqual(lost.status, CopyStatus.LOST)
        self.assertIn("lost", check_borrow(self.member, lost).first_problem)

    def test_reference_books_cannot_be_borrowed(self):
        ref = book("Dictionary", reference_only=True)
        self.assertIn("Reference only", check_borrow(self.member, ref.copies.first()).first_problem)

    def test_borrow_raises_with_reason_and_changes_nothing(self):
        self.member.status = MemberStatus.INACTIVE
        self.member.save()
        with self.assertRaises(CirculationError):
            borrow(self.member, self.copy, staff=self.librarian)
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.status, CopyStatus.AVAILABLE)
        self.assertFalse(Loan.objects.exists())


class BorrowReturnTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.member = member()
        self.book = book("Atomic Habits", copies=2)
        self.copy = self.book.copies.first()

    def test_borrow_marks_copy_and_counts_popularity(self):
        loan = borrow(self.member, self.copy, staff=self.librarian)
        self.copy.refresh_from_db()
        self.book.refresh_from_db()
        self.assertEqual(self.copy.status, CopyStatus.BORROWED)
        self.assertEqual(loan.status, LoanStatus.ACTIVE)
        self.assertEqual(self.book.times_borrowed, 1)

    def test_return_on_time_has_no_fee(self):
        borrow(self.member, self.copy, staff=self.librarian, now=at(14))
        result = return_copy(self.copy, staff=self.librarian)
        self.assertEqual(result.days_overdue, 0)
        self.assertIsNone(result.fee)
        self.copy.refresh_from_db()
        self.assertEqual(self.copy.status, CopyStatus.AVAILABLE)

    def test_late_return_generates_final_fee(self):
        borrow(self.member, self.copy, staff=self.librarian, now=at(18))
        result = return_copy(self.copy, staff=self.librarian)
        self.assertEqual(result.days_borrowed, 18)
        self.assertEqual(result.days_overdue, 4)
        self.assertEqual(result.fee.amount, Decimal("200.00"))
        self.assertEqual(result.fee.kind, FeeKind.LATE)
        self.assertEqual(result.fee.status, FeeStatus.PENDING)

    def test_estimated_fee_while_still_overdue(self):
        loan = borrow(self.member, self.copy, staff=self.librarian, now=at(17))
        self.assertEqual(loan.fee_quote().amount, Decimal("150.00"))
        standing = member_standing(self.member)
        self.assertEqual(standing.estimated_fees, Decimal("150.00"))
        self.assertEqual(standing.outstanding_fees, Decimal("0"))

    def test_returning_a_copy_not_on_loan(self):
        with self.assertRaises(CirculationError):
            return_copy(self.copy, staff=self.librarian)

    def test_only_one_active_loan_per_copy_at_database_level(self):
        from django.db import IntegrityError, transaction

        borrow(self.member, self.copy, staff=self.librarian)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Loan.objects.create(copy=self.copy, member=member(), due_date=timezone.localdate())


class RenewalTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.member = member()
        self.book = book(copies=1)
        self.loan = borrow(self.member, self.book.copies.first(), staff=self.librarian, now=at(5))

    def test_renew_extends_due_date(self):
        old_due = self.loan.due_date
        loan = renew(self.loan, actor=self.member.user)
        self.assertEqual(loan.due_date, old_due + timedelta(days=7))
        self.assertEqual(loan.renew_count, 1)

    def test_renewal_limit(self):
        renew(self.loan, actor=self.member.user)
        with self.assertRaisesMessage(CirculationError, "already been renewed"):
            renew(self.loan, actor=self.member.user)

    def test_cannot_renew_when_someone_is_waiting(self):
        from apps.circulation.reservations import reserve

        reserve(member(), self.book)
        with self.assertRaisesMessage(CirculationError, "waiting"):
            renew(self.loan, actor=self.member.user)

    def test_cannot_renew_overdue_loan(self):
        overdue = borrow(self.member, book(copies=1).copies.first(), staff=self.librarian, now=at(20))
        with self.assertRaisesMessage(CirculationError, "Overdue"):
            renew(overdue, actor=self.member.user)


class LostBookTests(LibraryTestCase):
    def test_lost_workflow_closes_loan_and_charges_replacement(self):
        m = member()
        b = book("Clean Code", replacement_cost=Decimal("4200"))
        loan = borrow(m, b.copies.first(), staff=self.librarian, now=at(16))
        fees = mark_lost(loan, staff=self.librarian)
        loan.refresh_from_db()
        copy = BookCopy.objects.get(pk=loan.copy_id)
        self.assertEqual(loan.status, LoanStatus.LOST)
        self.assertEqual(copy.status, CopyStatus.LOST)
        kinds = {f.kind: f.amount for f in fees}
        self.assertEqual(kinds[FeeKind.REPLACEMENT], Decimal("4200"))
        self.assertEqual(kinds[FeeKind.LATE], Decimal("100.00"))
        self.assertTrue(m.user.notifications.filter(title__contains="lost").exists())

    def test_default_replacement_fee_is_configurable(self):
        settings(default_replacement_fee=Decimal("999"))
        loan = borrow(member(), book().copies.first(), staff=self.librarian)
        fees = mark_lost(loan, staff=self.librarian)
        self.assertEqual([f.amount for f in fees], [Decimal("999")])
