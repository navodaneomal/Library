import re
import unicodedata

_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(value: str) -> str:
    """Lower-case, strip accents and punctuation: 'Harry Potter & the Philosopher’s Stone'
    becomes 'harry potter the philosopher s stone'. Used for forgiving search."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("'", "").replace("’", "")
    return _NON_WORD.sub(" ", value).strip()


def compact(value: str) -> str:
    """Normalised text without spaces — matches ISBNs typed with or without hyphens."""
    return normalize(value).replace(" ", "")
