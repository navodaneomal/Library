from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from apps.core.config import library_settings

from .models import User, normalize_email


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember = forms.BooleanField(label="Keep me signed in", required=False, initial=True)

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])


class RegistrationForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    phone = forms.CharField(
        label="Phone number",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    address = forms.CharField(
        max_length=255, required=False, widget=forms.TextInput(attrs={"autocomplete": "street-address"})
    )
    date_of_birth = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date", "autocomplete": "bday"})
    )
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(
        label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        s = library_settings()
        self.fields["phone"].required = s.require_phone
        self.fields["address"].required = s.require_address
        self.fields["date_of_birth"].required = s.require_date_of_birth
        self.fields["password1"].help_text = "At least 8 characters. Avoid common words."

    def clean_email(self):
        email = normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists. Try signing in instead.")
        return email

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > timezone.localdate():
            raise forms.ValidationError("Date of birth can't be in the future.")
        return dob

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords don't match.")
        if p1:
            probe = User(email=cleaned.get("email"), full_name=cleaned.get("full_name", ""))
            try:
                validate_password(p1, probe)
            except forms.ValidationError as error:
                self.add_error("password1", error)
        return cleaned
