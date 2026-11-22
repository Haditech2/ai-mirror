from django.core.management.base import BaseCommand

from mirror.services.retention import run_retention


class Command(BaseCommand):
    help = "Delete source photos and generated portraits that have passed their retention window."

    def handle(self, *args, **options) -> None:
        portraits, photos = run_retention()
        self.stdout.write(
            self.style.SUCCESS(f"Removed {portraits} expired portrait(s) and {photos} source photo(s).")
        )
