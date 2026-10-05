import csv
from datetime import date
from decimal import Decimal

from django.http import StreamingHttpResponse

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    """Neutralise spreadsheet formula injection (e.g. a title starting with '=')."""
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


class _Echo:
    def write(self, value):
        return value


def csv_response(filename: str, columns: list[str], rows) -> StreamingHttpResponse:
    writer = csv.writer(_Echo())

    def stream():
        yield "﻿"  # BOM so Excel opens UTF-8 correctly
        yield writer.writerow([safe_cell(c) for c in columns])
        for row in rows:
            yield writer.writerow([safe_cell(c) for c in row])

    response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
