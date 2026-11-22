import os
import sys
import threading

from django.apps import AppConfig
from django.conf import settings


class MirrorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mirror"

    def ready(self) -> None:
        from . import signals  # noqa: F401

        if settings.IS_VERCEL:
            return
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command in {"makemigrations", "migrate", "test", "check", "collectstatic", "shell"}:
            return
        if command == "runserver" and os.environ.get("RUN_MAIN") != "true":
            return

        # Delay database access until Django has completed application startup.
        from .services.jobs import recover_and_enqueue_pending

        threading.Timer(0.25, recover_and_enqueue_pending).start()
