from __future__ import annotations

from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.utils.functional import SimpleLazyObject

from .models import EventSettings


def _load_event_settings():
    try:
        return EventSettings.load()
    except (OperationalError, ProgrammingError):
        return None


def application_settings(request) -> dict[str, object]:
    return {
        "event_settings": SimpleLazyObject(_load_event_settings),
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }
