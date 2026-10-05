from django.core.management.base import BaseCommand

from apps.core.backup import write_backup


class Command(BaseCommand):
    help = "Write a compressed backup of all library data to BACKUP_DIR (or --dir)."

    def add_arguments(self, parser):
        parser.add_argument("--dir", help="Directory to write to (defaults to BACKUP_DIR).")
        parser.add_argument("--with-media", action="store_true", help="Also archive uploaded cover images.")
        parser.add_argument("--keep", type=int, default=30, help="How many backups to keep (default 30, 0 = all).")

    def handle(self, *args, **options):
        for path in write_backup(options.get("dir"), with_media=options["with_media"], keep=options["keep"]):
            self.stdout.write(self.style.SUCCESS(f"Backup written: {path}"))
