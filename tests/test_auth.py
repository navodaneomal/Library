import re

from django.core import mail
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.core.middleware import reset_setup_cache
from apps.members.models import Member

from .factories import PASSWORD, LibraryTestCase, member, settings


class RegistrationTests(LibraryTestCase):
    def register(self, **overrides):
        data = {
            "full_name": "Navoda Perera",
            "email": "Navoda@Example.com",
            "phone": "0771234567",
            "address": "",
            "date_of_birth": "",
            "password1": "a-long-Secret-99",
            "password2": "a-long-Secret-99",
        }
        data.update(overrides)
        return self.client.post("/register/", data)

    def test_registration_creates_member_with_unique_id(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.register()
        self.assertRedirects(response, "/me/")
        m = Member.objects.get(user__email="navoda@example.com")
        year = timezone.localdate().year
        self.assertRegex(m.member_id, rf"^LIB-{year}-\d{{5}}$")
        subjects = [msg.subject for msg in mail.outbox]
        self.assertTrue(any("Welcome" in s for s in subjects))
        self.assertTrue(any("confirm your email" in s for s in subjects))
        self.assertTrue(m.user.check_password("a-long-Secret-99"))
        self.assertFalse(m.user.password.startswith("a-long"))  # hashed, never plain text

    def test_member_ids_increment(self):
        first = member()
        second = member()
        self.assertEqual(int(second.member_id[-5:]), int(first.member_id[-5:]) + 1)

    def test_duplicate_email_rejected(self):
        member(email="taken@example.org")
        response = self.register(email="TAKEN@example.org")
        self.assertContains(response, "already exists")

    def test_weak_and_mismatched_passwords(self):
        self.assertContains(self.register(password1="password", password2="password"), "too common")
        self.assertContains(self.register(password2="different-Secret-99"), "don&#x27;t match")

    def test_required_fields_are_configurable(self):
        settings(require_address=True)
        self.assertContains(self.register(), "This field is required")

    def test_registration_can_be_closed(self):
        settings(allow_self_registration=False)
        self.assertContains(self.client.get("/register/"), "Join at the library desk")

    def test_email_verification(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.register()
        body = next(m for m in mail.outbox if "confirm" in m.subject).body
        link = re.search(r"https?://\S+/verify-email/\S+/", body).group(0)
        path = link.split("8000", 1)[-1] if "8000" in link else "/" + link.split("/", 3)[3]
        self.client.get(path)
        self.assertTrue(User.objects.get(email="navoda@example.com").email_verified)

    def test_verification_link_not_stored_in_email_log(self):
        from apps.notifications.models import EmailLog

        with self.captureOnCommitCallbacks(execute=True):
            self.register()
        log = EmailLog.objects.get(template="verify_email")
        self.assertNotIn("verify-email", log.body_text)
        self.assertEqual(log.status, "sent")


class LoginTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.member = member(email="reader@example.org")

    def login(self, password=PASSWORD, email="reader@example.org", url="/login/"):
        return self.client.post(url, {"email": email, "password": password, "remember": "on"})

    def test_login_and_logout(self):
        self.assertRedirects(self.login(), "/me/")
        self.assertEqual(self.client.get("/me/").status_code, 200)
        self.assertRedirects(self.client.post("/logout/"), "/")
        self.assertRedirects(self.client.get("/me/"), "/login/?next=/me/")

    def test_logout_requires_post(self):
        self.login()
        self.assertEqual(self.client.get("/logout/").status_code, 405)

    def test_incorrect_password_has_friendly_message(self):
        response = self.login(password="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertContains(response, "don&#x27;t match", status_code=401)

    def test_email_is_case_insensitive(self):
        self.assertRedirects(self.login(email="READER@example.org"), "/me/")

    @override_settings(LOGIN_RATE_LIMIT_PER_ACCOUNT=3)
    def test_rate_limit_locks_account_after_failures(self):
        for _ in range(3):
            self.login(password="wrong")
        response = self.login()  # even the right password is refused while locked
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many sign-in attempts", status_code=429)

    def test_staff_login_rejects_members(self):
        response = self.login(url="/admin/login/")
        self.assertContains(response, "doesn&#x27;t have staff access", status_code=403)

    def test_staff_login_redirects_to_dashboard_and_audits(self):
        from apps.core.models import AuditLog

        response = self.client.post("/admin/login/", {"email": "admin@example.org", "password": PASSWORD})
        self.assertRedirects(response, "/admin/")
        self.assertTrue(AuditLog.objects.filter(action="auth.staff_login").exists())

    def test_open_redirect_is_blocked(self):
        response = self.client.post(
            "/login/?next=https://evil.example/", {"email": "reader@example.org", "password": PASSWORD}
        )
        self.assertRedirects(response, "/me/")

    def test_password_reset_email(self):
        response = self.client.post("/password/reset/", {"email": "reader@example.org"})
        self.assertRedirects(response, "/password/reset/sent/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/password/reset/", mail.outbox[0].body)

    def test_inactive_user_cannot_login(self):
        User.objects.filter(email="reader@example.org").update(is_active=False)
        self.assertEqual(self.login().status_code, 401)


class FirstRunSetupTests(LibraryTestCase):
    def test_redirects_to_setup_until_admin_exists(self):
        User.objects.filter(role=Role.ADMIN).delete()
        reset_setup_cache()
        self.assertRedirects(self.client.get("/"), "/setup/", fetch_redirect_response=False)
        response = self.client.post(
            "/setup/",
            {
                "library_name": "Kandy Public Library",
                "admin_name": "Head Librarian",
                "admin_email": "head@kandy.example",
                "admin_password": "a-very-Strong-pass-1",
                "currency": "lkr",
                "loan_period_days": 21,
                "late_fee_per_day": "25.00",
            },
        )
        self.assertRedirects(response, "/admin/")
        from apps.core.models import LibrarySettings

        s = LibrarySettings.load()
        self.assertEqual((s.library_name, s.currency, s.loan_period_days), ("Kandy Public Library", "LKR", 21))
        self.assertEqual(self.client.get("/setup/").status_code, 404)  # one time only

    @override_settings(SETUP_TOKEN="s3cret-token")
    def test_setup_token_required_when_configured(self):
        User.objects.filter(role=Role.ADMIN).delete()
        reset_setup_cache()
        response = Client().post(
            "/setup/",
            {
                "library_name": "X",
                "admin_name": "X",
                "admin_email": "x@x.example",
                "admin_password": "a-very-Strong-pass-1",
                "currency": "LKR",
                "loan_period_days": 14,
                "late_fee_per_day": "50",
                "setup_token": "wrong",
            },
        )
        self.assertContains(response, "setup token is not correct")
        self.assertFalse(User.objects.filter(role=Role.ADMIN).exists())
