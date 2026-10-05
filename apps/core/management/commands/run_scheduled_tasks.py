from django.core.management.base import BaseCommand

from apps.core.tasks import run_all


class Command(BaseCommand):
    help = "Expire reservations, send due/overdue reminders and retry failed emails. Run hourly."

    def handle(self, *args, **options):
        results = run_all()
        for name, value in results.items():
            self.stdout.write(f"{name}: {value}")
