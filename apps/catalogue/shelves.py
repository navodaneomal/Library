"""'Shelf Finder': turn a shelf code such as FIC-A-04 into
'Fiction → Shelf A → Row 04' so anyone can walk straight to the book."""

from .models import Category


def section_names() -> dict[str, str]:
    return {
        code.upper(): name for code, name in Category.objects.exclude(shelf_code="").values_list("shelf_code", "name")
    }


def describe(code: str, sections: dict[str, str] | None = None) -> list[str]:
    code = (code or "").strip().upper()
    if not code:
        return []
    parts = [p for p in code.replace("/", "-").replace(".", "-").split("-") if p]
    if sections is None:
        sections = section_names()
    steps = [sections.get(parts[0], parts[0])]
    labels = ["Shelf", "Row", "Position"]
    for index, part in enumerate(parts[1:]):
        label = labels[index] if index < len(labels) else ""
        steps.append(f"{label} {part}".strip())
    return steps
