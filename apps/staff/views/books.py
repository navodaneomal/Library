from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.catalogue import isbn_lookup
from apps.catalogue import search as catalogue_search
from apps.catalogue.availability import annotate_availability, for_book
from apps.catalogue.covers import fetch_remote_cover
from apps.catalogue.models import Book, BookCopy, Category, CopyStatus
from apps.catalogue.search import invalidate_vocabulary
from apps.catalogue.services import (
    DuplicateBarcode,
    add_copy,
    lookup_code,
    refresh_search_fields,
    save_book,
)
from apps.circulation import reservations as reservation_service
from apps.circulation.models import (
    OPEN_RESERVATION_STATUSES,
    Loan,
    LoanStatus,
    Reservation,
    ReservationStatus,
)
from apps.circulation.services import CirculationError, change_copy_state
from apps.core import audit
from apps.core.http import is_partial
from apps.core.permissions import require_capability
from apps.staff.forms import AddCopyForm, BookForm, CopyForm, NewBookForm


@require_capability("books.view")
def book_list(request):
    filters = catalogue_search.Filters.from_querydict(request.GET)
    show = request.GET.get("show", "")
    if filters.q or filters.category or filters.available:
        books = catalogue_search.search(filters).books
    else:
        books = annotate_availability(Book.objects.filter(is_archived=False).select_related("category")).order_by(
            "title"
        )
    if show == "archived":
        books = annotate_availability(Book.objects.filter(is_archived=True).select_related("category")).order_by(
            "title"
        )
        if filters.q:
            for token in filters.q.lower().split():
                books = books.filter(search_text__contains=token)
    page = Paginator(books, 30).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "filters": filters,
        "show": show,
        "categories": Category.objects.all(),
        "totals": {
            "titles": Book.objects.filter(is_archived=False).count(),
            "copies": BookCopy.objects.exclude(status=CopyStatus.WITHDRAWN).count(),
            "archived": Book.objects.filter(is_archived=True).count(),
        },
    }
    return render(request, "staff/books/list.html", context)


@require_capability("books.manage")
def add_book(request):
    """Scan first: existing barcode → show it; known ISBN → add a copy; new → form."""
    found = None
    if request.method == "POST":
        found = lookup_code(request.POST.get("code", ""))
        if found.kind == "new_isbn" and not is_partial(request):
            return redirect(f"{reverse('staff:book_new')}?isbn={found.isbn}")
        if found.kind == "unknown" and not is_partial(request):
            return redirect(f"{reverse('staff:book_new')}?barcode={found.code}")
    context = {"found": found}
    template = "staff/books/_add_scan_result.html" if is_partial(request) else "staff/books/add.html"
    return render(request, template, context)


def _create_extra_copies(book, count, *, shelf, condition, actor):
    created = []
    for _ in range(count or 0):
        created.append(add_copy(book, shelf_location=shelf, condition=condition, actor=actor))
    return created


@require_capability("books.manage")
def book_new(request):
    initial, lookup = {}, None
    if request.method == "GET":
        initial["barcode"] = request.GET.get("barcode", "")
        if request.GET.get("isbn"):
            initial["isbn"] = request.GET["isbn"]
            lookup = isbn_lookup.lookup(request.GET["isbn"])
            if lookup:
                initial.update(
                    {
                        "title": lookup["title"],
                        "subtitle": lookup["subtitle"],
                        "author_names": lookup["authors"],
                        "publisher_name": lookup["publisher"],
                        "publication_year": lookup["publication_year"],
                        "keywords": lookup["keywords"],
                        "cover_url": lookup["cover_url"],
                    }
                )
    form = NewBookForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                book = form.save(commit=False)
                form.apply_relations(book)
                book = save_book(book, author_names=data["author_names"], actor=request.user)
                if not book.cover and data.get("cover_url"):
                    cover = fetch_remote_cover(data["cover_url"], name_hint=book.slug[:40])
                    if cover:
                        book.cover.save(cover.name, cover, save=True)
                form.save_traits(book)
                first = add_copy(
                    book,
                    barcode=data["barcode"],
                    shelf_location=data["shelf_location"],
                    condition=data["condition"],
                    actor=request.user,
                )
                extra = _create_extra_copies(
                    book,
                    data.get("extra_copies"),
                    shelf=data["shelf_location"],
                    condition=data["condition"],
                    actor=request.user,
                )
        except DuplicateBarcode as dup:
            form.add_error("barcode", f"This barcode is already registered to '{dup.existing.book.title}'.")
        else:
            invalidate_vocabulary()
            copies = [first, *extra]
            audit.record(
                request.user,
                "book.created",
                f"Added '{book.title}' with {len(copies)} cop{'y' if len(copies) == 1 else 'ies'}",
                target=book,
            )
            messages.success(
                request, f"Book added — '{book.title}' with {len(copies)} cop{'y' if len(copies) == 1 else 'ies'}."
            )
            return redirect(f"{reverse('staff:book', args=[book.pk])}?labels={','.join(str(c.pk) for c in copies)}")
    return render(request, "staff/books/form.html", {"form": form, "is_new": True, "lookup": lookup})


@require_capability("books.manage")
@require_GET
def isbn_lookup_json(request):
    data = isbn_lookup.lookup(request.GET.get("isbn", ""))
    if not data:
        return JsonResponse({"found": False})
    return JsonResponse({"found": True, **data})


@require_capability("books.view")
def book_detail(request, pk):
    book = get_object_or_404(Book.objects.select_related("category", "publisher").prefetch_related("traits"), pk=pk)
    copies = list(book.copies.order_by("copy_number"))
    active_loans = {
        loan.copy_id: loan
        for loan in Loan.objects.filter(copy__book=book, status=LoanStatus.ACTIVE).select_related("member__user")
    }
    holds = {
        r.copy_id: r
        for r in Reservation.objects.filter(book=book, status=ReservationStatus.READY).select_related("member__user")
    }
    queue = (
        Reservation.objects.filter(book=book, status=ReservationStatus.WAITING)
        .select_related("member__user")
        .order_by("created_at", "id")
    )
    history = (
        Loan.objects.filter(copy__book=book)
        .select_related("member__user", "copy")
        .prefetch_related("fees")
        .order_by("-borrowed_at")[:25]
    )
    label_ids = request.GET.get("labels", "")
    context = {
        "book": book,
        "availability": for_book(book),
        "copy_rows": [{"copy": c, "loan": active_loans.get(c.pk), "hold": holds.get(c.pk)} for c in copies],
        "queue": queue,
        "history": history,
        "label_ids": label_ids if all(part.isdigit() for part in label_ids.split(",") if part) else "",
        "can_delete": not Loan.objects.filter(copy__book=book).exists() and not book.reservations.exists(),
        "has_active_loans": bool(active_loans),
    }
    return render(request, "staff/books/detail.html", context)


@require_capability("books.manage")
def book_edit(request, pk):
    book = get_object_or_404(Book, pk=pk)
    tracked = ("title", "isbn", "category_id", "loan_period_days", "reference_only")
    before = {f: str(getattr(book, f)) for f in tracked}  # before validation mutates the instance
    form = BookForm(request.POST or None, request.FILES or None, instance=book)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated = form.save(commit=False)
            form.apply_relations(updated)
            save_book(updated, author_names=form.cleaned_data["author_names"], actor=request.user)
            form.save_traits(updated)
        invalidate_vocabulary()
        after = {f: str(getattr(updated, f)) for f in tracked}
        audit.record(
            request.user, "book.updated", f"Edited '{updated.title}'", target=updated, changes=audit.diff(before, after)
        )
        messages.success(request, "Book details saved.")
        return redirect("staff:book", pk=book.pk)
    return render(request, "staff/books/form.html", {"form": form, "book": book, "is_new": False})


@require_capability("books.manage")
@require_POST
def book_archive(request, pk):
    book = get_object_or_404(Book, pk=pk)
    if request.POST.get("action") == "restore":
        book.is_archived = False
        book.save(update_fields=["is_archived", "updated_at"])
        audit.record(request.user, "book.restored", f"Restored '{book.title}' to the catalogue", target=book)
        messages.success(request, f"'{book.title}' is back in the catalogue.")
        return redirect("staff:book", pk=book.pk)
    if Loan.objects.filter(copy__book=book, status=LoanStatus.ACTIVE).exists():
        messages.error(request, "This book still has copies on loan. Return them before archiving.")
        return redirect("staff:book", pk=book.pk)
    with transaction.atomic():
        for reservation in Reservation.objects.filter(book=book, status__in=OPEN_RESERVATION_STATUSES):
            reservation_service.cancel(reservation, actor=request.user, reason="title archived")
        book.is_archived = True
        book.save(update_fields=["is_archived", "updated_at"])
    invalidate_vocabulary()
    audit.record(request.user, "book.archived", f"Archived '{book.title}'", target=book)
    messages.success(request, f"'{book.title}' was archived. It no longer appears in the catalogue.")
    return redirect("staff:book", pk=book.pk)


@require_capability("books.manage")
@require_POST
def book_delete(request, pk):
    book = get_object_or_404(Book, pk=pk)
    if Loan.objects.filter(copy__book=book).exists() or book.reservations.exists():
        messages.error(request, "Books with borrowing history can't be deleted — archive it instead.")
        return redirect("staff:book", pk=book.pk)
    title = book.title
    with transaction.atomic():
        audit.record(request.user, "book.deleted", f"Deleted '{title}' and its copies", target=book)
        book.copies.all().delete()
        book.delete()
    invalidate_vocabulary()
    messages.success(request, f"'{title}' was deleted.")
    return redirect("staff:books")


@require_capability("books.manage")
def copy_add(request, pk):
    book = get_object_or_404(Book, pk=pk)
    last_shelf = (
        book.copies.exclude(shelf_location="").order_by("-copy_number").values_list("shelf_location", flat=True).first()
    )
    form = AddCopyForm(
        request.POST or None, initial={"shelf_location": last_shelf or "", "barcode": request.GET.get("barcode", "")}
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                first = add_copy(
                    book,
                    barcode=data["barcode"],
                    shelf_location=data["shelf_location"],
                    condition=data["condition"],
                    actor=request.user,
                )
                extra = _create_extra_copies(
                    book,
                    data.get("extra_copies"),
                    shelf=data["shelf_location"],
                    condition=data["condition"],
                    actor=request.user,
                )
        except DuplicateBarcode as dup:
            form.add_error("barcode", f"This barcode is already registered to '{dup.existing.book.title}'.")
        else:
            copies = [first, *extra]
            audit.record(
                request.user,
                "copy.created",
                f"Added {len(copies)} cop{'y' if len(copies) == 1 else 'ies'} of '{book.title}'",
                target=book,
            )
            messages.success(request, f"Added {len(copies)} cop{'y' if len(copies) == 1 else 'ies'}.")
            return redirect(f"{reverse('staff:book', args=[book.pk])}?labels={','.join(str(c.pk) for c in copies)}")
    return render(request, "staff/books/copy_add.html", {"form": form, "book": book})


@require_capability("books.view")
def copy_detail(request, pk):
    copy = get_object_or_404(BookCopy.objects.select_related("book"), pk=pk)
    history = (
        Loan.objects.filter(copy=copy).select_related("member__user").prefetch_related("fees").order_by("-borrowed_at")
    )
    hold = Reservation.objects.filter(copy=copy, status=ReservationStatus.READY).select_related("member__user").first()
    loan = history.filter(status=LoanStatus.ACTIVE).first()
    return render(
        request, "staff/books/copy_detail.html", {"copy": copy, "history": history[:50], "hold": hold, "loan": loan}
    )


@require_capability("books.manage")
def copy_edit(request, pk):
    copy = get_object_or_404(BookCopy.objects.select_related("book"), pk=pk)
    before = {"barcode": copy.barcode, "shelf_location": copy.shelf_location}
    form = CopyForm(request.POST or None, instance=copy)
    if request.method == "POST" and form.is_valid():
        status, condition = form.cleaned_data.get("status", copy.status), form.cleaned_data["condition"]
        try:
            with transaction.atomic():
                BookCopy.objects.filter(pk=copy.pk).update(
                    barcode=form.cleaned_data["barcode"],
                    shelf_location=form.cleaned_data["shelf_location"],
                    acquired_on=form.cleaned_data["acquired_on"],
                    notes=form.cleaned_data["notes"],
                )
                change_copy_state(copy, status=status, condition=condition, actor=request.user)
        except CirculationError as error:
            form.add_error("status", error.message)
        else:
            refresh_search_fields(copy.book)
            changes = audit.diff(
                before, {"barcode": form.cleaned_data["barcode"], "shelf_location": form.cleaned_data["shelf_location"]}
            )
            if changes:
                audit.record(
                    request.user, "copy.updated", f"Edited {copy.book.title} {copy.label}", target=copy, changes=changes
                )
            messages.success(request, "Copy saved.")
            return redirect("staff:copy", pk=copy.pk)
    return render(request, "staff/books/copy_form.html", {"form": form, "copy": copy})


@require_capability("books.view")
def labels(request):
    ids = [int(part) for part in request.GET.get("copies", "").split(",") if part.isdigit()][:200]
    copies = BookCopy.objects.filter(pk__in=ids).select_related("book").order_by("book__title", "copy_number")
    if request.GET.get("book", "").isdigit():
        copies = (
            BookCopy.objects.filter(book_id=request.GET["book"])
            .exclude(status=CopyStatus.WITHDRAWN)
            .select_related("book")
        )
    return render(request, "staff/books/labels.html", {"copies": copies})
