"""Cover image handling: validate uploads, re-encode them (which strips any
embedded payloads), and scale them down so pages stay fast."""

import io
import logging
import urllib.request
import zlib
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIZE = (600, 900)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
REMOTE_COVER_HOSTS = {"covers.openlibrary.org"}
PLACEHOLDER_TONES = 8


def placeholder_tone(title: str) -> int:
    """Deterministic colour (1–8) for the generated placeholder cover."""
    return zlib.crc32((title or "").encode("utf-8")) % PLACEHOLDER_TONES + 1


def process_image(data: bytes, *, name_hint: str = "cover") -> ContentFile:
    if len(data) > settings.MAX_COVER_UPLOAD_BYTES:
        raise ValidationError("Cover images must be smaller than 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise ValidationError("Please upload a JPEG, PNG or WebP image.")
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("That file doesn't look like an image we can use.") from None
    image = image.convert("RGB")
    image.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=82, method=4)
    return ContentFile(out.getvalue(), name=f"{name_hint}.webp")


def process_upload(uploaded_file, *, name_hint: str = "cover") -> ContentFile:
    return process_image(uploaded_file.read(), name_hint=name_hint)


def fetch_remote_cover(url: str, *, name_hint: str = "cover") -> ContentFile | None:
    """Download a cover from an allow-listed host (prevents SSRF)."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or parsed.hostname not in REMOTE_COVER_HOSTS:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LibraryManagementSystem/1.0"})
        with urllib.request.urlopen(request, timeout=6) as response:  # noqa: S310 — host allow-listed
            data = response.read(settings.MAX_COVER_UPLOAD_BYTES + 1)
        processed = process_image(data, name_hint=name_hint)
        # Open Library returns a 1×1 pixel image when it has no cover.
        with Image.open(io.BytesIO(processed.read())) as check:
            if check.width < 20:
                return None
        processed.seek(0)
        return processed
    except (ValidationError, OSError, ValueError):
        logger.info("Could not fetch cover from %s", url, exc_info=True)
        return None
