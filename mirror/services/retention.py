from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from mirror.models import CapturedPhoto, EventSettings, GeneratedPortrait
from mirror.storage import storage


def run_retention() -> tuple[int, int]:
    event = EventSettings.load()
    now = timezone.now()
    portrait_cutoff = now - timedelta(hours=event.retention_hours)
    portraits = GeneratedPortrait.objects.filter(
        completed_at__lt=portrait_cutoff,
        deleted_at__isnull=True,
        status=GeneratedPortrait.Status.COMPLETED,
    )
    portrait_count = 0
    for portrait in portraits.iterator():
        storage.delete(portrait.storage_path)
        portrait.storage_path = ""
        portrait.status = GeneratedPortrait.Status.DELETED
        portrait.deleted_at = now
        portrait.save(update_fields=("storage_path", "status", "deleted_at", "updated_at"))
        portrait_count += 1

    photo_cutoff = now - timedelta(hours=2)
    photos = CapturedPhoto.objects.filter(created_at__lt=photo_cutoff, deleted_at__isnull=True)
    photo_count = 0
    for photo in photos.iterator():
        storage.delete(photo.storage_path)
        photo.storage_path = ""
        photo.deleted_at = now
        photo.save(update_fields=("storage_path", "deleted_at"))
        photo_count += 1
    return portrait_count, photo_count
