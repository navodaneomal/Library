"""PostgreSQL only: trigram index so substring search stays fast with tens of
thousands of books. Skipped on SQLite and when the extension is unavailable."""

import logging

from django.db import migrations, transaction

logger = logging.getLogger(__name__)


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    try:
        with transaction.atomic(using=schema_editor.connection.alias):
            with schema_editor.connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS catalogue_book_search_trgm "
                    "ON catalogue_book USING gin (search_text gin_trgm_ops)"
                )
    except Exception:  # noqa: BLE001 — search still works, just without the index
        logger.warning("pg_trgm not available; skipping trigram search index", exc_info=True)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("DROP INDEX IF EXISTS catalogue_book_search_trgm")


class Migration(migrations.Migration):
    dependencies = [("catalogue", "0001_initial")]

    operations = [migrations.RunPython(forwards, backwards)]
