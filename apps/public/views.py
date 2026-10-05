from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.catalogue import search as catalogue_search
from apps.catalogue.availability import annotate_availability, for_book
from apps.catalogue.covers import placeholder_tone
from apps.catalogue.models import Author, Book, Category, CopyStatus
from apps.catalogue.recommendations import get_recommender, popular_books
from apps.catalogue.shelves import describe, section_names
from apps.circulation import reservations as reservation_service
from apps.circulation.models import (
    OPEN_RESERVATION_STATUSES,
    Loan,
    LoanStatus,
    Reservation,
    ReservationStatus,
)
from apps.core.permissions import member_required

PAGE_SIZE = 24


def _member(request):
    return getattr(request.user, "member", None) if request.user.is_authenticated else None


def home(request):
    books = catalogue_search.visible_books().select_related("category")
    member = _member(request)
    context = {
        "recent": annotate_availability(books).order_by("-created_at")[:8],
        "available_now": annotate_availability(books.filter(catalogue_search.available_now_q())).order_by(
            "-times_borrowed", "title"
        )[:8],
        "popular": popular_books(days=7, limit=8),
        "categories": Category.objects.annotate(n=Count("books", filter=Q(books__is_archived=False))).filter(n__gt=0)[
            :12
        ],
        "recommended": get_recommender().for_member(member, limit=4) if member else [],
    }
    return render(request, "public/home.html", context)


def catalogue(request):
    filters = catalogue_search.Filters.from_querydict(request.GET)
    if request.GET.get("view") == "available":
        filters.available = True
    outcome = catalogue_search.search(filters)
    if outcome.direct and request.GET.get("exact") != "0":
        # Scanning or typing an exact ISBN / barcode goes straight to the book.
        return redirect(outcome.direct.get_absolute_url())
    page = Paginator(outcome.books, PAGE_SIZE).get_page(request.GET.get("page"))
    authors = list(
        Author.objects.annotate(n=Count("books", filter=Q(books__is_archived=False)))
        .filter(n__gt=0)
        .order_by("-n", "name")[:150]
    )
    authors.sort(key=lambda a: a.name)
    if filters.author and filters.author not in {a.pk for a in authors}:
        selected = Author.objects.filter(pk=filters.author).first()
        if selected:
            authors.insert(0, selected)
    context = {
        "outcome": outcome,
        "filters": filters,
        "page": page,
        "sorts": catalogue_search.SORTS,
        "categories": Category.objects.annotate(n=Count("books", filter=Q(books__is_archived=False))).filter(n__gt=0),
        "authors": authors,
        "languages": catalogue_search.visible_books()
        .order_by("language")
        .values_list("language", flat=True)
        .distinct(),
    }
    return render(request, "public/catalogue.html", context)


def reservation_state(member, book):
    """What the reserve button on a book page should say for this member."""
    if member is None:
        return {"state": "anonymous"}
    loan = Loan.objects.filter(member=member, copy__book=book, status=LoanStatus.ACTIVE).order_by("due_date").first()
    if loan:
        return {"state": "on_loan", "loan": loan}
    reservation = Reservation.objects.filter(member=member, book=book, status__in=OPEN_RESERVATION_STATUSES).first()
    if reservation:
        return {
            "state": "reserved",
            "reservation": reservation,
            "position": reservation_service.queue_position(reservation),
            "estimate": reservation_service.estimated_availability(reservation),
        }
    problems = reservation_service.reservation_problems(member, book)
    return {"state": "can_reserve" if not problems else "blocked", "problems": problems}


def book_detail(request, slug):
    book = get_object_or_404(
        Book.objects.select_related("category", "publisher").prefetch_related("authors", "traits"),
        slug=slug,
        is_archived=False,
    )
    availability = for_book(book)
    sections = section_names()
    shelves = sorted(
        {
            code
            for code in book.copies.filter(status=CopyStatus.AVAILABLE)
            .exclude(shelf_location="")
            .values_list("shelf_location", flat=True)
        }
    )
    context = {
        "book": book,
        "availability": availability,
        "shelves": [{"code": code, "steps": describe(code, sections)} for code in shelves],
        "queue_length": Reservation.objects.filter(book=book, status=ReservationStatus.WAITING).count(),
        "reserve": reservation_state(_member(request), book),
        "similar": get_recommender().similar_to(book, limit=6),
        "loan_period": book.loan_period_days,
    }
    return render(request, "public/book_detail.html", context)


@member_required
@require_POST
def reserve(request, slug):
    book = get_object_or_404(Book, slug=slug, is_archived=False)
    try:
        reservation = reservation_service.reserve(request.user.member, book, actor=request.user)
    except reservation_service.ReservationError as error:
        messages.error(request, str(error))
        return redirect(book.get_absolute_url())
    return redirect(reverse("portal:reservation", args=[reservation.pk]) + "?new=1")


@require_GET
def suggest(request):
    data = catalogue_search.suggest(request.GET.get("q", "")[:100])
    payload = {
        "books": [
            {
                "title": book.title,
                "authors": book.author_names,
                "url": book.get_absolute_url(),
                "available": for_book(book).is_available,
                "status": for_book(book).label,
                "tone": placeholder_tone(book.title),
            }
            for book in data["books"]
        ],
        "authors": [
            {"name": author.name, "url": f"{reverse('public:catalogue')}?author={author.pk}"}
            for author in data["authors"]
        ],
    }
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, max-age=30"
    return response
