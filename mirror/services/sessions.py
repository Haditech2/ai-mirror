from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone

from mirror.models import GuestSession
from mirror.storage import storage

GUEST_SESSION_KEY = "academic_mirror_guest_id"


def current_guest(request: HttpRequest, *, touch: bool = True) -> GuestSession | None:
    guest_id = request.session.get(GUEST_SESSION_KEY)
    if not guest_id:
        return None
    try:
        guest = GuestSession.objects.get(pk=guest_id)
    except (GuestSession.DoesNotExist, ValueError):
        request.session.pop(GUEST_SESSION_KEY, None)
        return None
    now = timezone.now()
    if guest.status != GuestSession.Status.ACTIVE or guest.expires_at < now:
        if guest.status == GuestSession.Status.ACTIVE:
            complete_guest_session(guest, "inactive")
        request.session.pop(GUEST_SESSION_KEY, None)
        return None
    if touch:
        guest.last_activity_at = now
        minimum_expiry = now + timedelta(minutes=5)
        if guest.expires_at < minimum_expiry:
            guest.expires_at = minimum_expiry
        guest.save(update_fields=("last_activity_at", "expires_at"))
    return guest


def guest_session_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        guest = current_guest(request)
        if not guest:
            messages.info(request, "Your private session has ended. Please begin again.")
            return redirect("guest:welcome")
        request.guest_session = guest
        return view(request, *args, **kwargs)

    return wrapped


def start_guest_session(request: HttpRequest) -> GuestSession:
    existing = current_guest(request, touch=False)
    if existing:
        complete_guest_session(existing, "cancelled")
    now = timezone.now()
    guest = GuestSession.objects.create(
        expires_at=now + timedelta(minutes=settings.GUEST_SESSION_MINUTES),
    )
    request.session.cycle_key()
    request.session[GUEST_SESSION_KEY] = str(guest.pk)
    return guest


def complete_guest_session(guest: GuestSession, reason: str) -> None:
    if guest.status != GuestSession.Status.ACTIVE:
        return
    now = timezone.now()
    guest.status = GuestSession.Status.COMPLETED if reason == "completed" else GuestSession.Status.ABANDONED
    guest.completion_reason = reason
    guest.completed_at = now
    guest.save(update_fields=("status", "completion_reason", "completed_at"))

    active_photo_ids = set(
        guest.portraits.filter(status__in=("queued", "processing")).values_list("photo_id", flat=True)
    )
    for photo in guest.captured_photos.filter(deleted_at__isnull=True):
        if photo.pk not in active_photo_ids:
            storage.delete(photo.storage_path)
            photo.storage_path = ""
            photo.deleted_at = now
            photo.save(update_fields=("storage_path", "deleted_at"))
