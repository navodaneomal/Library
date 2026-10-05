from django.conf import settings


def is_partial(request) -> bool:
    """True when the request came from our progressive-enhancement fetch helper."""
    return request.headers.get("X-Requested-With") == "fetch"


def client_ip(request) -> str | None:
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # The proxy appends the address it saw, so the last hop is trustworthy.
            return forwarded.split(",")[-1].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def safe_next(request, fallback: str) -> str:
    """Return ?next= / POST next only if it points back at this site."""
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback
