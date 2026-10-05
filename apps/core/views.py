import logging

from django.contrib.auth import login
from django.db import connection, transaction
from django.http import Http404, HttpResponse, HttpResponseServerError
from django.shortcuts import redirect, render
from django.template import loader
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import requires_csrf_token

from apps.accounts.models import Role, User

from . import audit, config
from .forms import SetupForm
from .middleware import setup_is_complete
from .models import LibrarySettings

logger = logging.getLogger(__name__)


@never_cache
def first_run_setup(request):
    """One-time wizard: creates the library settings and the first administrator."""
    if setup_is_complete():
        raise Http404
    form = SetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if User.objects.filter(email=data["admin_email"].lower()).exists():
            form.add_error("admin_email", "An account with this email already exists.")
            return render(request, "core/setup.html", {"form": form}, status=400)
        with transaction.atomic():
            settings_row = LibrarySettings.load()
            settings_row.library_name = data["library_name"]
            settings_row.currency = data["currency"]
            settings_row.loan_period_days = data["loan_period_days"]
            settings_row.late_fee_per_day = data["late_fee_per_day"]
            settings_row.library_email = data["admin_email"]
            settings_row.save()
            admin = User.objects.create_user(
                email=data["admin_email"],
                password=data["admin_password"],
                full_name=data["admin_name"],
                role=Role.ADMIN,
            )
            audit.record(admin, "system.setup", f"Created library '{settings_row.library_name}'", target=settings_row)
        config.invalidate()
        login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("staff:dashboard")
    return render(request, "core/setup.html", {"form": form})


def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001
        logger.exception("Health check failed")
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


@requires_csrf_token
def bad_request(request, exception=None):
    return render(request, "errors/400.html", status=400)


@requires_csrf_token
def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


@requires_csrf_token
def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    # Rendered without request context so it works even if the database is down.
    return HttpResponseServerError(loader.get_template("errors/500.html").render({}))


def csrf_failure(request, reason=""):
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "errors/csrf.html", status=403)
