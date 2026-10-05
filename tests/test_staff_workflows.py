from decimal import Decimal

from django.test import Client

from apps.catalogue.models import Book, BookCopy, CopyStatus
from apps.circulation.models import Loan, LoanStatus
from apps.circulation.services import borrow
from apps.fees.models import Fee
from apps.inventory.models import InventorySession, ScanResult
from apps.inventory.services import complete, record_scan

from .factories import LibraryTestCase, at, book, member

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class DeskTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.assistant)
        self.member = member(name="Navoda")
        self.book = book("The Hobbit", copies=2)
        self.copy = self.book.copies.first()

    def test_scan_member_scan_book_confirm_done(self):
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": self.member.member_id}, **FETCH)
        self.assertContains(r, "Navoda")
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": self.copy.barcode}, **FETCH)
        self.assertContains(r, "Ready to borrow")
        self.assertContains(r, "Confirm borrow")
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": ""}, **FETCH)  # Enter confirms
        self.assertContains(r, "Done — book lent")
        self.assertEqual(Loan.objects.get().member, self.member)

    def test_blocked_borrow_explains_why(self):
        Fee.objects.create(member=self.member, amount=Decimal("900"), description="Old")
        self.c.post("/admin/circulation/", {"action": "scan", "code": self.member.member_id})
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": self.copy.barcode}, **FETCH)
        self.assertContains(r, "Borrowing unavailable")
        self.assertContains(r, "exceed")
        self.assertFalse(Loan.objects.exists())

    def test_find_member_by_name(self):
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": "navo"}, **FETCH)
        self.assertContains(r, "Ready — scan a book")

    def test_works_without_javascript(self):
        r = self.c.post("/admin/circulation/", {"action": "scan", "code": self.member.member_id})
        self.assertRedirects(r, "/admin/circulation/")
        self.assertContains(self.c.get("/admin/circulation/"), "Navoda")

    def test_return_desk_calculates_everything(self):
        borrow(self.member, self.copy, staff=self.librarian, now=at(18))
        r = self.c.post("/admin/circulation/return/", {"code": self.copy.barcode}, **FETCH)
        self.assertContains(r, "Book returned")
        self.assertContains(r, "4 days")
        self.assertContains(r, "200.00")
        self.assertContains(r, "Back to shelf")

    def test_quick_scan_returns_and_lends(self):
        borrow(self.member, self.copy, staff=self.librarian)
        r = self.c.post("/admin/quick-scan/", {"code": self.copy.barcode}, **FETCH)
        self.assertContains(r, "Book returned")
        other = member(name="Kasun")
        self.c.post("/admin/quick-scan/", {"code": other.member_id}, **FETCH)
        r = self.c.post("/admin/quick-scan/", {"code": self.copy.barcode}, **FETCH)
        self.assertContains(r, "Ready to borrow")
        r = self.c.post("/admin/quick-scan/", {"code": ""}, **FETCH)
        self.assertContains(r, "Done")
        self.assertEqual(Loan.objects.get(status=LoanStatus.ACTIVE).member, other)

    def test_unknown_barcode(self):
        r = self.c.post("/admin/quick-scan/", {"code": "NOPE-123"}, **FETCH)
        self.assertContains(r, "Barcode not recognised")


class BookManagementTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.librarian)

    def test_scan_existing_barcode_shows_duplicate_warning(self):
        b = book("Existing", copies=1)
        copy = b.copies.first()
        r = self.c.post("/admin/books/add/", {"code": copy.barcode}, **FETCH)
        self.assertContains(r, "This barcode is already registered")
        self.assertContains(r, copy.copy_code)

    def test_scan_new_barcode_opens_form(self):
        r = self.c.post("/admin/books/add/", {"code": "LBL-0099"})
        self.assertRedirects(r, "/admin/books/new/?barcode=LBL-0099")

    def test_add_book_with_extra_copies(self):
        r = self.c.post(
            "/admin/books/new/",
            {
                "title": "The Pragmatic Programmer",
                "author_names": "David Thomas, Andrew Hunt",
                "isbn": "978-0-13-595705-9",
                "publisher_name": "Addison-Wesley",
                "publication_year": 2019,
                "new_category": "Technology",
                "language": "English",
                "barcode": "LBL-0001",
                "shelf_location": "tec-t-03",
                "condition": "new",
                "extra_copies": 2,
                "dna": "Practical: 9\nIdeas: 7",
            },
        )
        b = Book.objects.get(title="The Pragmatic Programmer")
        self.assertRedirects(
            r,
            f"/admin/books/{b.pk}/?labels="
            + ",".join(str(p) for p in b.copies.order_by("pk").values_list("pk", flat=True)),
        )
        self.assertEqual(b.isbn, "9780135957059")
        self.assertEqual(b.copies.count(), 3)
        self.assertEqual(b.author_names, "David Thomas, Andrew Hunt")
        self.assertEqual(b.category.name, "Technology")
        self.assertEqual(set(b.copies.values_list("shelf_location", flat=True)), {"TEC-T-03"})
        self.assertEqual(b.traits.count(), 2)

    def test_duplicate_barcode_rejected_by_form(self):
        existing = book("Existing", copies=1).copies.first()
        r = self.c.post(
            "/admin/books/new/",
            {"title": "New", "language": "English", "barcode": existing.barcode, "condition": "new"},
        )
        self.assertContains(r, "already registered")
        self.assertFalse(Book.objects.filter(title="New").exists())

    def test_edit_copy_without_touching_book(self):
        b = book("Edit Me", copies=1)
        copy = b.copies.first()
        r = self.c.post(
            f"/admin/copies/{copy.pk}/edit/",
            {
                "barcode": "NEW-BC-1",
                "shelf_location": "fic-b-02",
                "condition": "worn",
                "status": "maintenance",
                "notes": "",
            },
        )
        self.assertRedirects(r, f"/admin/copies/{copy.pk}/")
        copy.refresh_from_db()
        self.assertEqual(
            (copy.barcode, copy.shelf_location, copy.status), ("NEW-BC-1", "FIC-B-02", CopyStatus.MAINTENANCE)
        )
        b.refresh_from_db()
        self.assertEqual(b.title, "Edit Me")

    def test_archive_and_delete(self):
        never = book("Never Borrowed")
        self.c.post(f"/admin/books/{never.pk}/delete/")
        self.assertFalse(Book.objects.filter(pk=never.pk).exists())
        used = book("Used")
        loan = borrow(member(), used.copies.first(), staff=self.librarian)
        self.c.post(f"/admin/books/{used.pk}/archive/")
        used.refresh_from_db()
        self.assertFalse(used.is_archived)  # copies on loan block archiving
        from apps.circulation.services import return_copy

        return_copy(loan.copy, staff=self.librarian)
        self.c.post(f"/admin/books/{used.pk}/delete/")
        self.assertTrue(Book.objects.filter(pk=used.pk).exists())  # has history → can't delete
        self.c.post(f"/admin/books/{used.pk}/archive/")
        used.refresh_from_db()
        self.assertTrue(used.is_archived)
        self.assertEqual(self.client.get(used.get_absolute_url()).status_code, 404)

    def test_fee_payment_via_staff_view(self):
        fee = Fee.objects.create(member=member(), amount=Decimal("150"), description="Late")
        r = self.c.post(
            f"/admin/fees/{fee.pk}/pay/",
            {"amount": "150", "method": "cash", "paid_on": "2026-10-05", "reference": "RC-9"},
        )
        self.assertRedirects(r, f"/admin/fees/{fee.pk}/")
        fee.refresh_from_db()
        self.assertEqual(fee.status, "paid")
        self.assertEqual(fee.payments.get().reference, "RC-9")


class InventoryTests(LibraryTestCase):
    def test_inventory_detects_missing_misplaced_duplicate_unknown(self):
        b = book("Shelf Book", copies=3)
        c1, c2, c3 = b.copies.order_by("copy_number")
        BookCopy.objects.filter(pk=c3.pk).update(shelf_location="FIC-Z-09")
        session = InventorySession.objects.create(name="Fiction", scope_prefix="FIC", started_by=self.librarian)
        self.assertEqual(record_scan(session, c1.barcode, location="FIC-A", staff=self.librarian).result, ScanResult.OK)
        self.assertEqual(
            record_scan(session, c1.barcode, location="FIC-A", staff=self.librarian).result, ScanResult.DUPLICATE
        )
        self.assertEqual(
            record_scan(session, c3.barcode, location="FIC-A", staff=self.librarian).result,
            ScanResult.UNEXPECTED_LOCATION,
        )
        self.assertEqual(record_scan(session, "BOGUS", location="", staff=self.librarian).result, ScanResult.UNKNOWN)
        done = complete(session, staff=self.librarian)
        self.assertEqual(done.summary["missing"], 1)
        self.assertEqual(done.summary["missing_ids"], [c2.pk])
