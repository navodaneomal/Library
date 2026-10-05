from django.shortcuts import redirect
from django.urls import reverse

from . import config

_setup_complete = False


def reset_setup_cache():
    global _setup_complete
    _setup_complete = False


def setup_is_complete() -> bool:
    global _setup_complete
    if not _setup_complete:
        from apps.accounts.models import Role, User

        _setup_complete = User.objects.filter(role=Role.ADMIN, is_active=True).exists()
    return _setup_complete


class RequestSettingsMiddleware:
    """Load library settings at most once per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config.begin_request()
        try:
            return self.get_response(request)
        finally:
            config.end_request()


class FirstRunSetupMiddleware:
    """Until an administrator exists, send every page to the setup wizard."""

    EXEMPT_PREFIXES = ("/setup/", "/static/", "/media/", "/healthz")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.EXEMPT_PREFIXES) and not setup_is_complete():
            return redirect(reverse("setup"))
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Strict Content-Security-Policy: no inline scripts or styles anywhere."""

    CSP = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data: blob:",
            "font-src 'self'",
            "connect-src 'self'",
            "media-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=(), payment=()")
        if request.user.is_authenticated if hasattr(request, "user") else False:
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response
