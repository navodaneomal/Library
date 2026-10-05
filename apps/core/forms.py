from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.utils.crypto import constant_time_compare

from .models import LibrarySettings


class SetupForm(forms.Form):
    library_name = forms.CharField(max_length=120, initial="City Library")
    admin_name = forms.CharField(label="Your name", max_length=150)
    admin_email = forms.EmailField(label="Admin email")
    admin_password = forms.CharField(
        label="Admin password", widget=forms.PasswordInput(render_value=False), min_length=10
    )
    currency = forms.CharField(max_length=3, initial="LKR", help_text="Three-letter code, e.g. LKR, USD, GBP.")
    loan_period_days = forms.IntegerField(label="Default loan period (days)", min_value=1, max_value=365, initial=14)
    late_fee_per_day = forms.DecimalField(
        label="Late fee per day", min_value=0, max_digits=10, decimal_places=2, initial=50
    )
    setup_token = forms.CharField(
        label="Setup token",
        required=False,
        widget=forms.PasswordInput,
        help_text="The SETUP_TOKEN from your server configuration.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not settings.SETUP_TOKEN:
            del self.fields["setup_token"]

    def clean_currency(self):
        value = self.cleaned_data["currency"].strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise forms.ValidationError("Use a three-letter currency code such as LKR.")
        return value

    def clean_admin_password(self):
        password = self.cleaned_data["admin_password"]
        validate_password(password)
        return password

    def clean_setup_token(self):
        token = self.cleaned_data.get("setup_token", "")
        if not constant_time_compare(token, settings.SETUP_TOKEN):
            raise forms.ValidationError("That setup token is not correct.")
        return token


class LibrarySettingsForm(forms.ModelForm):
    SECTIONS = [
        ("Library", ["library_name", "library_email", "library_phone", "library_address", "opening_hours", "currency"]),
        (
            "Loans",
            ["loan_period_days", "max_active_loans", "max_renewals", "renewal_days", "allow_duplicate_title_loans"],
        ),
        (
            "Fees",
            [
                "grace_period_days",
                "late_fee_per_day",
                "max_late_fee",
                "default_replacement_fee",
                "block_on_overdue",
                "fee_block_threshold",
            ],
        ),
        ("Reservations", ["reservation_hold_days", "reservation_waiting_expiry_days", "max_reservations_per_member"]),
        ("Reminders", ["reminders_enabled", "due_soon_reminder_days", "overdue_reminder_interval_days"]),
        (
            "Registration",
            [
                "allow_self_registration",
                "member_id_prefix",
                "require_phone",
                "require_address",
                "require_date_of_birth",
                "require_email_verification",
            ],
        ),
    ]

    class Meta:
        model = LibrarySettings
        exclude = ["updated_by"]
        widgets = {"opening_hours": forms.Textarea(attrs={"rows": 4})}

    def sections(self):
        return [(title, [self[name] for name in names]) for title, names in self.SECTIONS]

    def clean_currency(self):
        return self.cleaned_data["currency"].strip().upper()

    def clean_member_id_prefix(self):
        return self.cleaned_data["member_id_prefix"].strip().upper()
