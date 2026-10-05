from django.views.decorators.cache import never_cache

from apps.accounts.views import handle_login


@never_cache
def staff_login(request):
    return handle_login(request, template="staff/login.html", staff_only=True)
