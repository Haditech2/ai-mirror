from __future__ import annotations

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from .models import EventSettings


def application_settings(request) -> dict[str, object]:
    try:
        event = EventSettings.load()
    except (OperationalError, ProgrammingError):
        event = None
    return {
        "event_settings": event,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }
