from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core import audit
from apps.core.config import library_settings
from apps.core.http import client_ip
from apps.members.services import create_member
from apps.notifications import notify

from . import ratelimit
from .forms import LoginForm, RegistrationForm
from .models import User
from .tokens import email_verification_token

LOCKED_MESSAGE = "Too many sign-in attempts. Please wait 15 minutes and try again."


def _safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def home_for(user) -> str:
    if user.is_staff_member:
        return reverse("staff:dashboard")
    return reverse("portal:dashboard")


def handle_login(request, *, template: str, staff_only: bool):
    if request.user.is_authenticated and request.method == "GET":
        if not staff_only or request.user.is_staff_member:
            return redirect(_safe_next(request, home_for(request.user)))
    form = LoginForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        ip = client_ip(request) or "unknown"
        account_key, ip_key = f"login:acct:{email}", f"login:ip:{ip}"
        window = settings.LOGIN_RATE_LIMIT_WINDOW
        if ratelimit.is_limited(account_key, settings.LOGIN_RATE_LIMIT_PER_ACCOUNT, window) or ratelimit.is_limited(
            ip_key, settings.LOGIN_RATE_LIMIT_PER_IP, window
        ):
            form.add_error(None, LOCKED_MESSAGE)
            status = 429
        else:
            user = authenticate(request, username=email, password=form.cleaned_data["password"])
            if user is None:
                ratelimit.hit(account_key)
                ratelimit.hit(ip_key)
                form.add_error(None, "That email and password don't match. Please try again.")
                status = 401
            elif staff_only and not user.is_staff_member:
                form.add_error(None, "This account doesn't have staff access.")
                status = 403
            else:
                ratelimit.reset(account_key)
                login(request, user)
                if not form.cleaned_data.get("remember"):
                    request.session.set_expiry(0)
                if user.is_staff_member:
                    audit.record(user, "auth.staff_login", f"{user.full_name} signed in", request=request)
                return redirect(_safe_next(request, home_for(user)))
    return render(
        request,
        template,
        {"form": form, "next": request.GET.get("next") or request.POST.get("next", "")},
        status=status,
    )


@never_cache
def login_view(request):
    return handle_login(request, template="accounts/login.html", staff_only=False)


@require_POST
def logout_view(request):
    was_staff = request.user.is_authenticated and request.user.is_staff_member
    logout(request)
    messages.success(request, "You've been signed out.")
    return redirect("staff:login" if was_staff else "public:home")


def send_verification(request, user: User):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    link = f"{settings.SITE_URL}{reverse('accounts:verify_email', args=[uid, token])}"
    notify.verification(user, link)


@never_cache
def register(request):
    s = library_settings()
    if not s.allow_self_registration:
        return render(request, "accounts/registration_closed.html")
    if request.user.is_authenticated:
        return redirect(home_for(request.user))
    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        ip_key = f"register:ip:{client_ip(request) or 'unknown'}"
        if ratelimit.is_limited(ip_key, 10, 3600):
            form.add_error(None, "Too many registrations from this network. Please try again later.")
        elif form.is_valid():
            ratelimit.hit(ip_key)
            data = form.cleaned_data
            member = create_member(
                full_name=data["full_name"],
                email=data["email"],
                password=data["password1"],
                phone=data.get("phone", ""),
                address=data.get("address", ""),
                date_of_birth=data.get("date_of_birth"),
            )
            notify.welcome(member)
            send_verification(request, member.user)
            login(request, member.user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, f"Welcome to {s.library_name}! Your member ID is {member.member_id}.")
            return redirect("portal:dashboard")
    return render(request, "accounts/register.html", {"form": form})


def verify_email(request, uidb64: str, token: str):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None
    if user is None or not email_verification_token.check_token(user, token):
        return render(request, "accounts/verify_failed.html", status=400)
    if not user.email_verified_at:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    messages.success(request, "Thank you — your email address is verified.")
    return redirect("portal:dashboard" if request.user.is_authenticated else "accounts:login")


@login_required
@require_POST
def resend_verification(request):
    key = f"verify:user:{request.user.pk}"
    if request.user.email and not request.user.email_verified:
        if ratelimit.is_limited(key, 3, 3600):
            messages.error(request, "We've already sent a few emails. Please check your inbox (and spam folder).")
        else:
            ratelimit.hit(key)
            send_verification(request, request.user)
            messages.success(request, f"We've sent a new verification link to {request.user.email}.")
    return redirect(_safe_next(request, reverse("portal:profile")))


class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    email_template_name = "emails/password_reset.txt"
    html_email_template_name = "emails/password_reset.html"
    subject_template_name = "emails/password_reset.subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def get_extra_email_context(self):
        s = library_settings()
        return {"library": s, "library_name": s.library_name, "site_url": settings.SITE_URL}

    def form_valid(self, form):
        key = f"pwreset:ip:{client_ip(self.request) or 'unknown'}"
        if ratelimit.is_limited(key, 5, 3600):
            return redirect(self.success_url)  # don't reveal anything; quietly stop sending
        ratelimit.hit(key)
        self.extra_email_context = self.get_extra_email_context()
        return super().form_valid(form)


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"

    def get_success_url(self):
        return home_for(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed.")
        return super().form_valid(form)
