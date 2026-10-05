"""Backup & export helpers. A backup is a gzip-compressed JSON dump of all
library data that `python manage.py restore` loads into any database
(SQLite or PostgreSQL), so it also works for moving servers."""

import gzip
import io
import tarfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command

APP_LABELS = ["core", "accounts", "members", "catalogue", "circulation", "fees", "notifications", "inventory"]
EXCLUDE = ["accounts.ratelimitevent"]


def dump_json() -> bytes:
    buffer = io.StringIO()
    call_command("dumpdata", *APP_LABELS, exclude=EXCLUDE, indent=None, stdout=buffer)
    return gzip.compress(buffer.getvalue().encode("utf-8"))


def backup_filename(prefix: str = "library") -> str:
    return f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.json.gz"


def write_backup(directory: Path | None = None, *, with_media: bool = False, keep: int = 30) -> list[Path]:
    directory = Path(directory or settings.BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    data_path = directory / backup_filename()
    data_path.write_bytes(dump_json())
    written = [data_path]
    if with_media and Path(settings.MEDIA_ROOT).exists():
        media_path = directory / data_path.name.replace(".json.gz", "-media.tar.gz")
        with tarfile.open(media_path, "w:gz") as archive:
            archive.add(settings.MEDIA_ROOT, arcname="media")
        written.append(media_path)
    # Keep the newest `keep` data backups.
    backups = sorted(directory.glob("library-*.json.gz"))
    for old in backups[:-keep] if keep else []:
        old.unlink(missing_ok=True)
        media = old.with_name(old.name.replace(".json.gz", "-media.tar.gz"))
        media.unlink(missing_ok=True)
    return written
