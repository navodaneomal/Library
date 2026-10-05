from datetime import date

from django.http import Http404
from django.shortcuts import render

from apps.catalogue.models import Category
from apps.core.permissions import require_capability
from apps.reports import queries
from apps.reports.csv_export import csv_response
from apps.staff.forms import DateRangeForm

REPORTS = {
    "borrowing": ("Borrowing", "Books borrowed in a period, by member and category."),
    "overdue": ("Overdue", "Every overdue book with its member and current fee."),
    "popular": ("Popular books", "The most borrowed titles in a period."),
    "inventory": ("Inventory", "Copies by status, and those needing attention."),
    "fees": ("Fees", "Fees generated, paid, waived and outstanding."),
}


@require_capability("reports")
def report_index(request):
    return render(request, "staff/reports/index.html", {"reports": REPORTS})


@require_capability("reports")
def report_view(request, slug):
    if slug not in REPORTS:
        raise Http404
    form = DateRangeForm(request.GET or None)
    date_from = date_to = None
    if form.is_valid():
        date_from, date_to = form.cleaned_data["date_from"], form.cleaned_data["date_to"]
    if slug == "borrowing":
        table = queries.borrowing(
            date_from,
            date_to,
            member_query=request.GET.get("member", "").strip(),
            category=request.GET.get("category", ""),
        )
    elif slug == "overdue":
        table = queries.overdue()
    elif slug == "popular":
        table = queries.popular(date_from, date_to)
    elif slug == "inventory":
        table = queries.inventory()
    else:
        table = queries.fees(date_from, date_to)
    if request.GET.get("format") == "csv":
        return csv_response(f"{slug}-report-{date.today():%Y%m%d}.csv", table.columns, table.rows)
    if not form.is_bound:
        start, end = queries.default_range(None, None)
        form = DateRangeForm(initial={"date_from": start, "date_to": end})
    return render(
        request,
        "staff/reports/report.html",
        {
            "slug": slug,
            "report": REPORTS[slug],
            "table": table,
            "form": form,
            "uses_dates": slug in ("borrowing", "popular", "fees"),
            "categories": Category.objects.all() if slug == "borrowing" else [],
        },
    )
