"""Small helpers for building test data through the real services."""

import itertools
from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalogue.models import Book, Category
from apps.catalogue.services import add_copy, save_book
from apps.core import config
from apps.core.middleware import reset_setup_cache
from apps.core.models import LibrarySettings
from apps.members.services import create_member

PASSWORD = "Correct-Horse-42"
_counter = itertools.count(1)


def at(days_ago: int, hour: int = 10):
    """An aware datetime `days_ago` days before today (local time)."""
    day = timezone.localdate() - timedelta(days=days_ago)
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def staff(role=Role.LIBRARIAN, email=None, name=None):
    n = next(_counter)
    return User.objects.create_user(
        email=email or f"{role}{n}@example.org", password=PASSWORD, full_name=name or f"{role.title()} {n}", role=role
    )


def member(name=None, email=None, **extra):
    n = next(_counter)
    return create_member(
        full_name=name or f"Member {n}",
        email=email if email is not None else f"member{n}@example.org",
        password=PASSWORD,
        phone="0771234567",
        **extra,
    )


def book(title=None, copies=1, author="Test Author", category=None, **fields):
    n = next(_counter)
    obj = Book(title=title or f"Book {n}", **fields)
    if category:
        obj.category = Category.objects.get_or_create(
            name=category, defaults={"slug": category.lower().replace(" ", "-")}
        )[0]
    save_book(obj, author_names=author)
    for i in range(copies):
        add_copy(obj, barcode=f"T{n:04d}{i + 1:03d}", shelf_location="FIC-A-01")
    return obj


def settings(**values):
    s = LibrarySettings.load()
    for key, value in values.items():
        setattr(s, key, value)
    s.save()
    config.invalidate()
    return s


class LibraryTestCase(TestCase):
    """Every test starts with a configured library and an administrator."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        reset_setup_cache()
        self.settings_row = settings(
            library_name="Test Library",
            loan_period_days=14,
            late_fee_per_day=50,
            grace_period_days=0,
            max_late_fee=1000,
            fee_block_threshold=500,
        )
        self.admin = staff(Role.ADMIN, email="admin@example.org", name="Ada Admin")
        self.librarian = staff(Role.LIBRARIAN)
        self.assistant = staff(Role.ASSISTANT)

    def tearDown(self):
        reset_setup_cache()
        super().tearDown()
