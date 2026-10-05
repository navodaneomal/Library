from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Restore a backup created by `manage.py backup` (or downloaded from Admin → Backup)."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to a library-*.json.gz backup file.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Erase ALL current data first. Required if the database is not empty.",
        )

    def handle(self, *args, **options):
        if User.objects.exists() and not options["replace"]:
            raise CommandError("This database already contains data. Re-run with --replace to erase it and restore.")
        with transaction.atomic():
            if options["replace"]:
                call_command("flush", interactive=False, verbosity=0)
            call_command("loaddata", options["file"], verbosity=1)
        self.stdout.write(self.style.SUCCESS("Restore complete. Restart the application to clear any cached settings."))
