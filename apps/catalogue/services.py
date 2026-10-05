"""Catalogue operations: creating books and copies, keeping search fields
fresh, and interpreting scanned codes."""

import re
import secrets
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils.text import slugify

from apps.barcodes.isbn import to_isbn10, to_isbn13
from apps.barcodes.scan import normalize_code
from apps.core.text import normalize

from .models import Author, Book, BookCopy, CopyStatus

_AUTHOR_SPLIT = re.compile(r"\s*[,;&]\s*")
_STOPWORDS = {"THE", "A", "AN"}


# ---------------------------------------------------------------------------
# Search fields
# ---------------------------------------------------------------------------


def refresh_search_fields(book: Book, *, save: bool = True) -> None:
    """Denormalised, accent-free text used for fast, forgiving search."""
    parts = [book.title, book.subtitle, book.author_names, book.keywords, book.language]
    if book.isbn:
        parts.append(book.isbn)
        isbn10 = to_isbn10(book.isbn)
        if isbn10:
            parts.append(isbn10)
    if book.category_id:
        parts.append(book.category.name)
    if book.publisher_id:
        parts.append(book.publisher.name)
    if book.publication_year:
        parts.append(str(book.publication_year))
    if book.pk:
        for barcode, code in book.copies.values_list("barcode", "copy_code"):
            parts.extend([barcode, code])
    book.search_title = normalize(book.full_title)[:255]
    book.search_text = " ".join(normalize(p) for p in parts if p)
    if save and book.pk:
        Book.objects.filter(pk=book.pk).update(search_title=book.search_title, search_text=book.search_text)


def unique_slug(title: str, *, exclude_pk: int | None = None) -> str:
    base = slugify(title)[:240] or "book"
    slug, n = base, 2
    qs = Book.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def parse_author_names(raw: str) -> list[str]:
    names, seen = [], set()
    for name in _AUTHOR_SPLIT.split(raw or ""):
        name = " ".join(name.split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name[:200])
    return names


def set_authors(book: Book, raw: str) -> None:
    names = parse_author_names(raw)
    authors = []
    for name in names:
        author = Author.objects.filter(name__iexact=name).first() or Author.objects.create(name=name)
        authors.append(author)
    book.authors.set(authors)
    book.author_names = ", ".join(a.name for a in authors)
    Book.objects.filter(pk=book.pk).update(author_names=book.author_names)


@transaction.atomic
def save_book(book: Book, *, author_names: str, actor=None) -> Book:
    """Create or update a book together with its authors and search fields."""
    if book.isbn:
        book.isbn = to_isbn13(book.isbn) or book.isbn
    if not book.slug:
        book.slug = unique_slug(book.title, exclude_pk=book.pk)
    if not book.pk and actor is not None:
        book.created_by = actor
    book.save()
    set_authors(book, author_names)
    refresh_search_fields(book)
    from .search import invalidate_vocabulary

    transaction.on_commit(invalidate_vocabulary)
    invalidate_vocabulary()
    return book


# ---------------------------------------------------------------------------
# Copies
# ---------------------------------------------------------------------------


def _code_stem(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title.upper())]
    significant = [w for w in words if w not in _STOPWORDS] or words or ["BK"]
    return (significant[0] + "XXX")[:3]


def generate_barcode() -> str:
    """12-digit numeric barcode in the 20–29 'internal use' range."""
    while True:
        candidate = "20" + "".join(secrets.choice("0123456789") for _ in range(10))
        if not BookCopy.objects.filter(barcode=candidate).exists():
            return candidate


def barcode_conflict(barcode: str, *, exclude_copy_pk: int | None = None) -> BookCopy | None:
    qs = BookCopy.objects.select_related("book").filter(barcode=barcode)
    if exclude_copy_pk:
        qs = qs.exclude(pk=exclude_copy_pk)
    return qs.first()


class DuplicateBarcode(Exception):
    def __init__(self, existing: BookCopy):
        super().__init__("This barcode is already registered.")
        self.existing = existing


@transaction.atomic
def add_copy(
    book: Book,
    *,
    barcode: str = "",
    shelf_location: str = "",
    condition: str = "good",
    acquired_on=None,
    notes: str = "",
    actor=None,
    allocate: bool = True,
) -> BookCopy:
    """Register a physical copy. Never silently duplicates a barcode."""
    from apps.circulation.reservations import allocate_copy  # avoid import cycle

    barcode = normalize_code(barcode) or generate_barcode()
    existing = barcode_conflict(barcode)
    if existing:
        raise DuplicateBarcode(existing)
    Book.objects.select_for_update().filter(pk=book.pk).first()  # serialise copy numbering per book
    number = (book.copies.aggregate(n=Max("copy_number"))["n"] or 0) + 1
    try:
        with transaction.atomic():
            copy = BookCopy.objects.create(
                book=book,
                copy_number=number,
                copy_code=f"tmp-{secrets.token_hex(8)}",
                barcode=barcode,
                shelf_location=normalize_code(shelf_location),
                condition=condition,
                acquired_on=acquired_on,
                notes=notes,
                status=CopyStatus.AVAILABLE,
            )
    except IntegrityError:
        existing = barcode_conflict(barcode)
        if existing:
            raise DuplicateBarcode(existing) from None
        raise
    from apps.core.config import library_settings

    prefix = library_settings().member_id_prefix
    copy.copy_code = f"{prefix}-{_code_stem(book.title)}-{copy.pk:05d}"
    copy.save(update_fields=["copy_code"])
    refresh_search_fields(book)
    if allocate:
        allocate_copy(copy)
    return copy


# ---------------------------------------------------------------------------
# Scanned codes
# ---------------------------------------------------------------------------


@dataclass
class ScanLookup:
    code: str
    kind: str  # "copy" | "member" | "book" | "new_isbn" | "unknown" | "empty"
    copy: BookCopy | None = None
    book: Book | None = None
    member: object | None = None
    isbn: str | None = None


def lookup_code(raw: str) -> ScanLookup:
    """Work out what a scanned barcode refers to."""
    from apps.members.models import Member

    code = normalize_code(raw)
    if not code:
        return ScanLookup(code=code, kind="empty")
    copy = BookCopy.objects.select_related("book", "book__category").filter(barcode=code).first()
    if copy:
        return ScanLookup(code=code, kind="copy", copy=copy, book=copy.book)
    member = Member.objects.select_related("user").filter(member_id=code).first()
    if member:
        return ScanLookup(code=code, kind="member", member=member)
    copy = BookCopy.objects.select_related("book").filter(copy_code=code).first()
    if copy:
        return ScanLookup(code=code, kind="copy", copy=copy, book=copy.book)
    isbn = to_isbn13(code)
    if isbn:
        book = Book.objects.filter(isbn=isbn).first()
        if book:
            return ScanLookup(code=code, kind="book", book=book, isbn=isbn)
        return ScanLookup(code=code, kind="new_isbn", isbn=isbn)
    return ScanLookup(code=code, kind="unknown")
