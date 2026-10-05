"""Dependency-free Code 128 encoder producing crisp SVG barcodes.

Used for member library cards and copy labels. Code set B covers printable
ASCII; runs of four or more digits switch to code set C (two digits per
symbol) to keep numeric barcodes compact.
"""

from html import escape

# Bar/space widths for symbol values 0–105, followed by the stop pattern.
PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()

START_B, START_C = 104, 105
SWITCH_TO_C, SWITCH_TO_B = 99, 100
STOP = 106
QUIET_ZONE = 10  # modules of white space each side


class BarcodeError(ValueError):
    pass


def _digit_run(data: str, start: int) -> int:
    end = start
    while end < len(data) and data[end].isdigit():
        end += 1
    return end - start


def encode(data: str) -> list[int]:
    """Return the symbol values (start, data, checksum, stop) for `data`."""
    if not data:
        raise BarcodeError("Nothing to encode.")
    if any(not (32 <= ord(ch) <= 126) for ch in data):
        raise BarcodeError("Code 128 labels support printable ASCII characters only.")

    values: list[int] = []
    current: str | None = None

    def use(code_set: str):
        nonlocal current
        if current == code_set:
            return
        if current is None:
            values.append(START_B if code_set == "B" else START_C)
        else:
            values.append(SWITCH_TO_B if code_set == "B" else SWITCH_TO_C)
        current = code_set

    i = 0
    while i < len(data):
        run = _digit_run(data, i)
        whole_numeric = i == 0 and run == len(data) and run >= 2 and run % 2 == 0
        if run >= 4 or whole_numeric:
            if run % 2:
                use("B")
                values.append(ord(data[i]) - 32)
                i += 1
                run -= 1
            use("C")
            while run >= 2:
                values.append(int(data[i : i + 2]))
                i += 2
                run -= 2
        else:
            use("B")
            values.append(ord(data[i]) - 32)
            i += 1

    checksum = values[0] + sum(position * value for position, value in enumerate(values[1:], start=1))
    values.append(checksum % 103)
    values.append(STOP)
    return values


def modules(data: str) -> list[int]:
    """Alternating bar/space widths (in modules), starting with a bar."""
    widths: list[int] = []
    for value in encode(data):
        widths.extend(int(w) for w in PATTERNS[value])
    return widths


def svg(data: str, *, height: int = 60, module_width: float = 2.0, label: str | None = None) -> str:
    """Return an SVG string. The caller marks it safe; `data` is escaped here."""
    widths = modules(data)
    total_modules = sum(widths) + 2 * QUIET_ZONE
    width = total_modules * module_width
    x = QUIET_ZONE * module_width
    path = []
    for index, w in enumerate(widths):
        span = w * module_width
        if index % 2 == 0:  # bars are at even positions
            path.append(f"M{x:g} 0h{span:g}v{height}h-{span:g}z")
        x += span
    aria = escape(label or f"Barcode {data}")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height}" '
        f'width="{width:g}" height="{height}" role="img" aria-label="{aria}" class="barcode-svg" '
        f'preserveAspectRatio="none" shape-rendering="crispEdges">'
        f'<rect width="100%" height="100%" fill="#fff"/>'
        f'<path d="{"".join(path)}" fill="#000"/></svg>'
    )
