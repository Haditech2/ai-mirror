from __future__ import annotations

import threading
import time

from django.db import OperationalError, ProgrammingError

from mirror.services.retention import run_retention

_retention_lock = threading.Lock()
_last_retention_run = 0.0


class SecurityHeadersMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # "same-origin", not "no-referrer": with no-referrer, Chrome/Edge send `Origin: null` on
        # form POSTs, which Django's CSRF origin check rejects (403) - every kiosk form failed.
        # same-origin still sends nothing to other sites, so signed links never leak externally.
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
        if request.path.startswith(("/camera", "/portrait", "/collection", "/fitting")):
            response.headers.setdefault("Cache-Control", "private, no-store, max-age=0")
        return response


class RetentionMiddleware:
    """Run lightweight privacy cleanup opportunistically at most once per hour."""

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        global _last_retention_run
        now = time.monotonic()
        if now - _last_retention_run >= 3600 and _retention_lock.acquire(blocking=False):
            try:
                if time.monotonic() - _last_retention_run >= 3600:
                    try:
                        run_retention()
                        _last_retention_run = time.monotonic()
                    except (OperationalError, ProgrammingError):
                        pass
            finally:
                _retention_lock.release()
        return self.get_response(request)
