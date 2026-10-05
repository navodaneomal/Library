"""ISBN-10 / ISBN-13 validation and normalisation."""

import re

_STRIP = re.compile(r"[\s\-]")


def clean(value: str | None) -> str:
    return _STRIP.sub("", value or "").upper()


def is_valid_isbn10(value: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", value):
        return False
    total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(value))
    return total % 11 == 0


def is_valid_isbn13(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value):
        return False
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(value))
    return total % 10 == 0


def isbn13_check_digit(first12: str) -> str:
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(first12))
    return str((10 - total % 10) % 10)


def to_isbn13(value: str | None) -> str | None:
    """Return the ISBN-13 form of a valid ISBN-10/13, otherwise None."""
    value = clean(value)
    if is_valid_isbn13(value):
        return value
    if is_valid_isbn10(value):
        body = "978" + value[:9]
        return body + isbn13_check_digit(body)
    return None


def to_isbn10(isbn13: str) -> str | None:
    if not is_valid_isbn13(isbn13) or not isbn13.startswith("978"):
        return None
    body = isbn13[3:12]
    total = sum((10 - i) * int(ch) for i, ch in enumerate(body))
    check = (11 - total % 11) % 11
    return body + ("X" if check == 10 else str(check))


def looks_like_isbn(value: str | None) -> bool:
    return to_isbn13(value) is not None
