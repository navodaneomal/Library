"""Seed the demo library and open every page as every kind of user."""

import gzip
import io
import json
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import Client, TestCase, TransactionTestCase

from apps.accounts.models import User
from apps.catalogue.models import Book, BookCopy
from apps.core.middleware import reset_setup_cache
from apps.fees.models import Fee
from apps.members.models import Member


class DemoSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reset_setup_cache()
        call_command("seed_demo", password="Demo-pass-123", stdout=io.StringIO())

    def setUp(self):
        reset_setup_cache()

    def get_all(self, client, urls):
        for url in urls:
            with self.subTest(url=url):
                response = client.get(url)
                self.assertLess(response.status_code, 400, url)

    def test_seed_data_meets_requirements(self):
        self.assertGreaterEqual(Book.objects.count(), 20)
        self.assertGreater(BookCopy.objects.count(), Book.objects.count())
        self.assertGreaterEqual(Member.objects.count(), 5)
        from apps.circulation.models import Loan, Reservation

        self.assertTrue(Loan.objects.filter(status="returned").exists())
        self.assertTrue(Loan.objects.overdue().exists())
        self.assertTrue(Reservation.objects.filter(status="waiting").exists())
        self.assertTrue(Reservation.objects.filter(status="ready").exists())
        self.assertTrue(Fee.objects.filter(status="pending").exists())
        self.assertTrue(Fee.objects.filter(status="paid").exists())

    def test_every_page_renders(self):
        book = Book.objects.first()
        copy = BookCopy.objects.first()
        member = Member.objects.first()
        fee = Fee.objects.first()
        self.get_all(
            Client(),
            [
                "/",
                "/books/",
                "/books/?q=harry",
                f"/books/{book.slug}/",
                "/login/",
                "/register/",
                "/admin/login/",
                "/healthz",
            ],
        )
        m = Client()
        m.force_login(User.objects.get(email="navoda@example.com"))
        self.get_all(
            m,
            ["/me/", "/me/books/", "/me/reservations/", "/me/fees/", "/me/notifications/", "/me/profile/", "/me/card/"],
        )
        a = Client()
        a.force_login(User.objects.get(email="admin@library.local"))
        self.get_all(
            a,
            [
                "/admin/",
                "/admin/today/",
                "/admin/circulation/",
                "/admin/circulation/return/",
                "/admin/quick-scan/",
                "/admin/books/",
                "/admin/books/add/",
                "/admin/books/new/",
                f"/admin/books/{book.pk}/",
                f"/admin/books/{book.pk}/edit/",
                f"/admin/books/{book.pk}/copies/add/",
                f"/admin/copies/{copy.pk}/",
                f"/admin/copies/{copy.pk}/edit/",
                f"/admin/books/labels/?book={book.pk}",
                "/admin/members/",
                "/admin/members/new/",
                f"/admin/members/{member.pk}/",
                f"/admin/members/{member.pk}/edit/",
                f"/admin/members/{member.pk}/card/",
                "/admin/reservations/",
                "/admin/overdue/",
                "/admin/fees/",
                f"/admin/fees/{fee.pk}/",
                "/admin/inventory/",
                "/admin/reports/",
                "/admin/reports/borrowing/",
                "/admin/reports/overdue/",
                "/admin/reports/popular/",
                "/admin/reports/inventory/",
                "/admin/reports/fees/",
                "/admin/settings/",
                "/admin/staff/",
                "/admin/staff/new/",
                "/admin/audit/",
                "/admin/backup/",
                "/admin/announcements/",
            ],
        )

    def test_csv_exports(self):
        a = Client()
        a.force_login(User.objects.get(email="admin@library.local"))
        for url in [
            "/admin/reports/overdue/?format=csv",
            "/admin/backup/export/books.csv",
            "/admin/backup/export/members.csv",
        ]:
            response = a.get(url)
            self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
            body = b"".join(response.streaming_content).decode("utf-8-sig")
            self.assertGreater(len(body.splitlines()), 1)

    def test_backup_download_contains_all_data(self):
        a = Client()
        a.force_login(User.objects.get(email="admin@library.local"))
        response = a.post("/admin/backup/download/")
        data = json.loads(gzip.decompress(response.content))
        models = {row["model"] for row in data}
        for label in [
            "catalogue.book",
            "catalogue.bookcopy",
            "members.member",
            "circulation.loan",
            "fees.fee",
            "core.librarysettings",
        ]:
            self.assertIn(label, models)


class BackupRestoreTests(TransactionTestCase):
    """Runs outside a wrapping transaction, exactly like the real CLI."""

    def setUp(self):
        reset_setup_cache()
        call_command("seed_demo", password="Demo-pass-123", stdout=io.StringIO())

    def tearDown(self):
        reset_setup_cache()

    def test_backup_and_restore_round_trip(self):
        counts = (Book.objects.count(), BookCopy.objects.count(), Member.objects.count(), Fee.objects.count())
        with tempfile.TemporaryDirectory() as directory:
            call_command("backup", dir=directory, stdout=io.StringIO())
            backup_file = next(Path(directory).glob("library-*.json.gz"))
            with self.assertRaises(CommandError):
                call_command("restore", str(backup_file), stdout=io.StringIO())  # refuses without --replace
            call_command("restore", str(backup_file), replace=True, stdout=io.StringIO())
        self.assertEqual(
            counts, (Book.objects.count(), BookCopy.objects.count(), Member.objects.count(), Fee.objects.count())
        )
        self.assertTrue(User.objects.get(email="admin@library.local").check_password("Demo-pass-123"))
