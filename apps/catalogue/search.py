"""Fast, forgiving catalogue search.

* Every word must match somewhere (title, author, ISBN, barcode, category,
  publisher, keywords…). Partial words work: 'harr pott' finds Harry Potter.
* Accents and punctuation are ignored.
* An exact ISBN or copy barcode jumps straight to that book.
* When nothing matches, a 'did you mean' correction is tried automatically.
"""

import difflib
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When

from apps.barcodes.isbn import to_isbn13
from apps.barcodes.scan import normalize_code
from apps.core.text import normalize

from .availability import annotate_availability
from .models import UNUSABLE_CONDITIONS, Author, Book, BookCopy, CopyStatus

SORTS = {
    "relevance": "Best match",
    "title": "Title A–Z",
    "newest": "Newest arrivals",
    "popular": "Most popular",
    "year": "Publication year",
}
VOCAB_CACHE_KEY = "catalogue:search-vocabulary:v1"


@dataclass
class Filters:
    q: str = ""
    available: bool = False
    author: int | None = None
    category: str = ""
    language: str = ""
    year_from: int | None = None
    year_to: int | None = None
    sort: str = "relevance"

    @classmethod
    def from_querydict(cls, data) -> "Filters":
        def as_int(name):
            try:
                return int(data.get(name, ""))
            except (TypeError, ValueError):
                return None

        sort = data.get("sort", "relevance")
        return cls(
            q=(data.get("q", "") or "").strip()[:200],
            available=data.get("available") in {"1", "on", "true"},
            author=as_int("author"),
            category=(data.get("category", "") or "")[:90],
            language=(data.get("language", "") or "")[:40],
            year_from=as_int("year_from"),
            year_to=as_int("year_to"),
            sort=sort if sort in SORTS else "relevance",
        )

    @property
    def active_count(self) -> int:
        return sum(
            bool(v) for v in (self.available, self.author, self.category, self.language, self.year_from, self.year_to)
        )


@dataclass
class Outcome:
    books: object  # queryset
    filters: Filters
    corrected: str | None = None
    direct: Book | None = None
    tokens: list[str] = field(default_factory=list)


def visible_books():
    return Book.objects.filter(is_archived=False)


def available_now_q() -> Q:
    usable_available = BookCopy.objects.filter(book=OuterRef("pk"), status=CopyStatus.AVAILABLE).exclude(
        condition__in=UNUSABLE_CONDITIONS
    )
    return Q(Exists(usable_available))


def _apply_tokens(queryset, tokens: list[str]):
    for token in tokens:
        queryset = queryset.filter(search_text__contains=token)
    return queryset


def _vocabulary() -> list[str]:
    vocab = cache.get(VOCAB_CACHE_KEY)
    if vocab is None:
        words = set()
        for title in visible_books().values_list("search_title", flat=True).iterator():
            words.update(w for w in title.split() if len(w) > 2)
        for name in Author.objects.values_list("name", flat=True).iterator():
            words.update(w for w in normalize(name).split() if len(w) > 2)
        vocab = sorted(words)
        cache.set(VOCAB_CACHE_KEY, vocab, 300)
    return vocab


def invalidate_vocabulary():
    cache.delete(VOCAB_CACHE_KEY)


def did_you_mean(tokens: list[str]) -> str | None:
    vocab = _vocabulary()
    vocab_set = set(vocab)
    corrected, changed = [], False
    for token in tokens:
        if len(token) < 3 or token in vocab_set or token.isdigit():
            corrected.append(token)
            continue
        match = difflib.get_close_matches(token, vocab, n=1, cutoff=0.72)
        if match:
            corrected.append(match[0])
            changed = True
        else:
            corrected.append(token)
    return " ".join(corrected) if changed else None


def _rank(queryset, phrase: str):
    return queryset.annotate(
        rank=Case(
            When(search_title=phrase, then=Value(0)),
            When(search_title__startswith=phrase, then=Value(1)),
            When(search_title__contains=phrase, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    )


def direct_match(query: str) -> Book | None:
    isbn = to_isbn13(query)
    if isbn:
        book = visible_books().filter(isbn=isbn).first()
        if book:
            return book
    code = normalize_code(query)
    if code and " " not in code:
        copy = BookCopy.objects.select_related("book").filter(barcode=code, book__is_archived=False).first()
        if copy:
            return copy.book
    return None


def search(filters: Filters) -> Outcome:
    queryset = visible_books().select_related("category", "publisher")
    outcome = Outcome(books=queryset, filters=filters)
    phrase = normalize(filters.q)

    if phrase:
        outcome.direct = direct_match(filters.q)
        tokens = phrase.split()
        matched = _apply_tokens(queryset, tokens)
        if not outcome.direct and not matched.exists():
            corrected = did_you_mean(tokens)
            if corrected:
                corrected_matches = _apply_tokens(queryset, corrected.split())
                if corrected_matches.exists():
                    matched, phrase, tokens = corrected_matches, corrected, corrected.split()
                    outcome.corrected = corrected
        queryset = matched
        outcome.tokens = tokens

    if filters.available:
        queryset = queryset.filter(available_now_q())
    if filters.author:
        queryset = queryset.filter(authors__id=filters.author)
    if filters.category:
        queryset = queryset.filter(category__slug=filters.category)
    if filters.language:
        queryset = queryset.filter(language__iexact=filters.language)
    if filters.year_from:
        queryset = queryset.filter(publication_year__gte=filters.year_from)
    if filters.year_to:
        queryset = queryset.filter(publication_year__lte=filters.year_to)

    queryset = annotate_availability(queryset)
    sort = filters.sort
    if sort == "relevance" and phrase:
        queryset = _rank(queryset, phrase).order_by("rank", "-copies_available", "-times_borrowed", "title")
    elif sort == "newest":
        queryset = queryset.order_by("-created_at", "title")
    elif sort == "popular":
        queryset = queryset.order_by("-times_borrowed", "title")
    elif sort == "year":
        queryset = queryset.order_by("-publication_year", "title")
    else:
        queryset = queryset.order_by("title")
    outcome.books = queryset
    return outcome


def suggest(query: str, limit: int = 6) -> dict:
    """Autocomplete data: matching titles and authors."""
    phrase = normalize(query)
    if len(phrase) < 2:
        return {"books": [], "authors": []}
    tokens = phrase.split()
    books = _rank(_apply_tokens(visible_books(), tokens), phrase)
    books = annotate_availability(books).order_by("rank", "-times_borrowed", "title")[:limit]
    author_q = Q()
    for token in tokens:
        author_q &= Q(name__icontains=token)
    authors = Author.objects.filter(author_q, books__is_archived=False).distinct()[:3]
    return {"books": list(books), "authors": list(authors)}
