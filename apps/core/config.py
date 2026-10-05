"""Access to the administrator-configurable library settings.

Settings are read once per request (memoised in a context variable that the
RequestSettingsMiddleware resets), so every worker process always sees fresh
values without hitting the database repeatedly within one request.
"""

from contextvars import ContextVar

from .models import LibrarySettings

_request_settings: ContextVar[LibrarySettings | None] = ContextVar("library_settings", default=None)
_memo_active: ContextVar[bool] = ContextVar("library_settings_memo", default=False)


def library_settings() -> LibrarySettings:
    if _memo_active.get():
        cached = _request_settings.get()
        if cached is None:
            cached = LibrarySettings.load()
            _request_settings.set(cached)
        return cached
    return LibrarySettings.load()


def begin_request():
    _memo_active.set(True)
    _request_settings.set(None)


def end_request():
    _request_settings.set(None)
    _memo_active.set(False)


def invalidate():
    _request_settings.set(None)
