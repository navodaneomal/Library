from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

from apps.core import views as core_views

urlpatterns = [
    path("", include("apps.public.urls")),
    path("", include("apps.accounts.urls")),
    path("me/", include("apps.portal.urls")),
    path("admin/", include("apps.staff.urls")),
    path("setup/", core_views.first_run_setup, name="setup"),
    path("healthz", core_views.healthz, name="healthz"),
]

if settings.SERVE_MEDIA:
    # Small installs can let Django serve uploaded covers; larger ones should
    # let the reverse proxy (Caddy / nginx) serve /media directly.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
    ]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
