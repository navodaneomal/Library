"""Optional metadata lookup from Open Library so that scanning an ISBN fills
in most of the 'Add book' form. Failure is silent: staff just type it in."""

import json
import logging
import re
import urllib.request

from django.conf import settings

from apps.barcodes.isbn import to_isbn13

logger = logging.getLogger(__name__)
API = "https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"


def parse_response(isbn: str, payload: dict) -> dict | None:
    record = payload.get(f"ISBN:{isbn}")
    if not record:
        return None
    year = None
    match = re.search(r"(1[5-9]\d\d|20\d\d)", record.get("publish_date", "") or "")
    if match:
        year = int(match.group(1))
    subjects = [s.get("name", "") for s in record.get("subjects", [])[:6] if s.get("name")]
    cover = (record.get("cover") or {}).get("large") or (record.get("cover") or {}).get("medium") or ""
    return {
        "isbn": isbn,
        "title": record.get("title", ""),
        "subtitle": record.get("subtitle", ""),
        "authors": ", ".join(a.get("name", "") for a in record.get("authors", []) if a.get("name")),
        "publisher": ((record.get("publishers") or [{}])[0]).get("name", ""),
        "publication_year": year,
        "keywords": ", ".join(subjects),
        "cover_url": cover.replace("http://", "https://"),
    }


def lookup(raw_isbn: str) -> dict | None:
    isbn = to_isbn13(raw_isbn)
    if not isbn or not settings.ISBN_LOOKUP_ENABLED:
        return None
    try:
        request = urllib.request.Request(API.format(isbn=isbn), headers={"User-Agent": "LibraryManagementSystem/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — fixed https host
            payload = json.loads(response.read(512 * 1024).decode("utf-8"))
        return parse_response(isbn, payload)
    except (OSError, ValueError):
        logger.info("ISBN lookup failed for %s", isbn, exc_info=True)
        return None
