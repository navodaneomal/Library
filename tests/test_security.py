from django.test import Client

from apps.accounts.models import Role
from apps.circulation.reservations import reserve
from apps.circulation.services import borrow
from apps.core.models import AuditLog
from apps.core.permissions import CAPABILITIES
from apps.reports.csv_export import safe_cell

from .factories import LibraryTestCase, book, member


class AccessControlTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.reader = member()
        self.member_client = Client()
        self.member_client.force_login(self.reader.user)

    def as_role(self, role):
        c = Client()
        c.force_login({"admin": self.admin, "librarian": self.librarian, "assistant": self.assistant}[role])
        return c

    def test_anonymous_admin_access_redirects_to_staff_login(self):
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/admin/login/?next=/admin/")

    def test_member_gets_access_denied(self):
        response = self.member_client.get("/admin/")
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied.", status_code=403)

    def test_assistant_cannot_change_settings_or_manage_books(self):
        c = self.as_role("assistant")
        for url in [
            "/admin/settings/",
            "/admin/staff/",
            "/admin/books/new/",
            "/admin/audit/",
            "/admin/backup/",
            "/admin/reports/",
        ]:
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertEqual(c.post("/admin/settings/", {"loan_period_days": 365}).status_code, 403)
        for url in ["/admin/", "/admin/circulation/", "/admin/quick-scan/", "/admin/reservations/", "/admin/members/"]:
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_librarian_cannot_manage_staff_or_settings(self):
        c = self.as_role("librarian")
        self.assertEqual(c.get("/admin/settings/").status_code, 403)
        self.assertEqual(
            c.post("/admin/staff/new/", {"full_name": "X", "email": "x@x.org", "role": "admin"}).status_code, 403
        )
        self.assertEqual(c.get("/admin/books/new/").status_code, 200)

    def test_every_staff_view_is_protected(self):
        """Walk the URL conf: every /admin view except login must declare a capability."""
        from django.urls import get_resolver

        unprotected = []
        for pattern in get_resolver().url_patterns:
            if str(pattern.pattern) != "admin/":
                continue
            for sub in pattern.url_patterns:
                view = sub.callback
                if sub.name == "login":
                    continue
                if getattr(view, "required_capability", None) not in CAPABILITIES:
                    unprotected.append(sub.name)
        self.assertEqual(unprotected, [])

    def test_staff_cannot_escalate_own_role(self):
        c = self.as_role("librarian")
        c.post(
            f"/admin/staff/{self.librarian.pk}/",
            {"full_name": "L", "email": self.librarian.email, "role": "admin", "is_active": "on"},
        )
        self.librarian.refresh_from_db()
        self.assertEqual(self.librarian.role, Role.LIBRARIAN)

    def test_last_admin_cannot_be_demoted(self):
        c = self.as_role("admin")
        response = c.post(
            f"/admin/staff/{self.admin.pk}/",
            {"full_name": "A", "email": self.admin.email, "role": "librarian", "is_active": "on"},
        )
        self.assertContains(response, "at least one active administrator")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, Role.ADMIN)


class PrivacyTests(LibraryTestCase):
    def test_public_book_page_never_names_borrower(self):
        b = book("Private Matters", copies=1)
        borrower = member(name="Secret Borrower")
        borrow(borrower, b.copies.first(), staff=self.librarian)
        response = self.client.get(b.get_absolute_url())
        self.assertContains(response, "Borrowed")
        self.assertNotContains(response, "Secret Borrower")
        self.assertNotContains(response, borrower.member_id)

    def test_members_only_see_their_own_data(self):
        b = book(copies=2)
        alice, bob = member(name="Alice"), member(name="Bob")
        alice_loan = borrow(alice, b.copies.all()[0], staff=self.librarian)
        bob_res = reserve(bob, book(copies=1))
        c = Client()
        c.force_login(alice.user)
        self.assertEqual(c.get(f"/me/reservations/{bob_res.pk}/").status_code, 404)
        self.assertEqual(c.post(f"/me/reservations/{bob_res.pk}/cancel/").status_code, 404)
        bob_client = Client()
        bob_client.force_login(bob.user)
        self.assertEqual(bob_client.post(f"/me/books/{alice_loan.pk}/renew/").status_code, 404)
        self.assertNotContains(bob_client.get("/me/books/"), "Alice")


class WebSecurityTests(LibraryTestCase):
    def test_csrf_is_enforced(self):
        c = Client(enforce_csrf_checks=True)
        response = c.post("/login/", {"email": "admin@example.org", "password": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "This page expired", status_code=403)

    def test_html_in_titles_is_escaped(self):
        b = book('<script>alert("x")</script>', copies=1)
        response = self.client.get(b.get_absolute_url())
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_security_headers(self):
        response = self.client.get("/")
        csp = response["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_no_inline_scripts_or_styles_in_pages(self):
        import re

        c = Client()
        c.force_login(self.admin)
        for url in ["/", "/books/", "/admin/", "/admin/circulation/", "/admin/settings/"]:
            html = c.get(url).content.decode()
            self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), url)
            self.assertNotIn(' style="', html, url)

    def test_csv_formula_injection_neutralised(self):
        self.assertEqual(safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(safe_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(safe_cell("Normal"), "Normal")

    def test_sql_injection_in_search_is_harmless(self):
        book("Safe Book")
        response = self.client.get("/books/", {"q": "'; DROP TABLE catalogue_book; --"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/books/?q=safe").status_code == 200)

    def test_invalid_input_gets_friendly_errors(self):
        c = Client()
        c.force_login(self.librarian)
        response = c.post("/admin/books/new/", {"title": "", "isbn": "123", "condition": "new", "language": "English"})
        self.assertContains(response, "This field is required")
        self.assertContains(response, "ISBN doesn")

    def test_settings_changes_are_audited(self):
        c = Client()
        c.force_login(self.admin)
        from apps.core.forms import LibrarySettingsForm
        from apps.core.models import LibrarySettings

        form = LibrarySettingsForm(instance=LibrarySettings.load())
        data = {}
        for name in form.fields:
            value = form.initial.get(name)
            if isinstance(value, bool):
                if value:
                    data[name] = "on"
            elif value is not None:
                data[name] = value
        data["loan_period_days"] = 21
        response = c.post("/admin/settings/", data)
        self.assertRedirects(response, "/admin/settings/")
        self.assertEqual(LibrarySettings.load().loan_period_days, 21)
        entry = AuditLog.objects.get(action="settings.updated")
        self.assertEqual(entry.changes["loan_period_days"], ["14", "21"])
        self.assertEqual(entry.actor, self.admin)
