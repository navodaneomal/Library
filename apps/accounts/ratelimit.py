"""Tiny database-backed rate limiter (works across all worker processes)."""

from datetime import timedelta

from django.utils import timezone

from .models import RateLimitEvent


def count(key: str, window_seconds: int) -> int:
    since = timezone.now() - timedelta(seconds=window_seconds)
    return RateLimitEvent.objects.filter(key=key, created_at__gte=since).count()


def is_limited(key: str, limit: int, window_seconds: int) -> bool:
    return count(key, window_seconds) >= limit


def hit(key: str) -> None:
    RateLimitEvent.objects.create(key=key[:200])


def reset(key: str) -> None:
    RateLimitEvent.objects.filter(key=key).delete()


def cleanup(older_than_seconds: int = 24 * 3600) -> int:
    cutoff = timezone.now() - timedelta(seconds=older_than_seconds)
    deleted, _ = RateLimitEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted
