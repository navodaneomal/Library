"""Normalising raw scanner input.

USB barcode scanners behave like keyboards: they type the code and press
Enter. We strip whitespace / control characters and upper-case the result so
that a typed 'lib-2026-00001' matches a scanned 'LIB-2026-00001'.
"""

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_LENGTH = 64


def normalize_code(raw: str | None) -> str:
    value = _CONTROL.sub("", raw or "").strip().upper()
    return value[:MAX_LENGTH]
