"""'Quiet recommendations' — deliberately simple and explainable.

Scores candidate books by shared author, shared category and popularity,
based on what a member has borrowed before. The Recommender interface lets
a smarter (e.g. AI-based) strategy be plugged in through the
RECOMMENDER_CLASS setting without changing any view.
"""

from collections import Counter
from datetime import timedelta
from typing import Protocol

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.module_loading import import_string

from .availability import annotate_availability
from .models import Book


class Recommender(Protocol):
    def for_member(self, member, limit: int = 6) -> list[Book]: ...

    def similar_to(self, book: Book, limit: int = 6) -> list[Book]: ...


def popular_books(days: int = 7, limit: int = 8):
    """Most borrowed in the last `days` (falls back to all-time popularity)."""
    since = timezone.now() - timedelta(days=days)
    recent = (
        annotate_availability(Book.objects.filter(is_archived=False))
        .annotate(recent_loans=Count("copies__loans", filter=Q(copies__loans__borrowed_at__gte=since), distinct=True))
        .filter(recent_loans__gt=0)
        .select_related("category")
        .order_by("-recent_loans", "-times_borrowed", "title")[:limit]
    )
    books = list(recent)
    if len(books) < limit:
        seen = {b.pk for b in books}
        extra = (
            annotate_availability(Book.objects.filter(is_archived=False, times_borrowed__gt=0).exclude(pk__in=seen))
            .select_related("category")
            .order_by("-times_borrowed", "title")[: limit - len(books)]
        )
        books.extend(extra)
    return books


class SimpleRecommender:
    history_size = 40

    def for_member(self, member, limit: int = 6) -> list[Book]:
        from apps.circulation.models import Loan

        history = list(
            Loan.objects.filter(member=member)
            .select_related("copy__book")
            .order_by("-borrowed_at")[: self.history_size]
        )
        if not history:
            return []
        read_ids = {loan.copy.book_id for loan in history}
        categories = Counter(loan.copy.book.category_id for loan in history if loan.copy.book.category_id)
        authors = Counter(
            author_id
            for author_id in Book.authors.through.objects.filter(book_id__in=read_ids).values_list(
                "author_id", flat=True
            )
        )
        if not categories and not authors:
            return []
        candidates = (
            annotate_availability(
                Book.objects.filter(is_archived=False, reference_only=False)
                .filter(Q(category_id__in=categories.keys()) | Q(authors__id__in=authors.keys()))
                .exclude(pk__in=read_ids)
                .distinct()
            )
            .select_related("category")
            .prefetch_related("authors")[:200]
        )
        max_pop = max((b.times_borrowed for b in candidates), default=0) or 1

        def score(book):
            author_score = sum(authors.get(a.pk, 0) for a in book.authors.all())
            return (
                3 * author_score
                + 2 * categories.get(book.category_id, 0)
                + book.times_borrowed / max_pop
                + (0.5 if book.copies_available else 0)
            )

        return sorted(candidates, key=score, reverse=True)[:limit]

    def similar_to(self, book: Book, limit: int = 6) -> list[Book]:
        author_ids = list(book.authors.values_list("pk", flat=True))
        query = Q(authors__id__in=author_ids)
        if book.category_id:
            query |= Q(category_id=book.category_id)
        candidates = (
            annotate_availability(Book.objects.filter(is_archived=False).filter(query).exclude(pk=book.pk).distinct())
            .select_related("category")
            .prefetch_related("authors")[:100]
        )

        def score(other):
            shared = len(set(author_ids) & {a.pk for a in other.authors.all()})
            return 3 * shared + (2 if other.category_id == book.category_id else 0) + other.times_borrowed / 100

        return sorted(candidates, key=score, reverse=True)[:limit]


def get_recommender() -> Recommender:
    return import_string(settings.RECOMMENDER_CLASS)()
