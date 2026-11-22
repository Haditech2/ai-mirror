from __future__ import annotations

import uuid
from datetime import date

from django.db import models
from django.utils import timezone


class StoredImageBlob(models.Model):
    path = models.CharField(max_length=500, primary_key=True)
    content = models.BinaryField()


class EventSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    professor_name = models.CharField(max_length=180, default="Professor Rashidah Funke Olarenwaju")
    event_title = models.CharField(max_length=140, default="Inaugural Lecture")
    event_date = models.DateField(default=date(2026, 11, 25))
    welcome_headline = models.CharField(
        max_length=220,
        default="Academic gown portraits for lecture guests",
    )
    welcome_text = models.TextField(
        default=(
            "Take one photo, choose a gown and scan a QR code to keep your portrait. "
            "Your face stays exactly as photographed."
        )
    )
    privacy_notice = models.TextField(
        default=(
            "Your photograph will be used only to create your selected ceremonial portrait. "
            "The original camera image is cleared after your session, while the finished "
            "portrait follows the event's configured deletion policy."
        )
    )
    inactivity_timeout_seconds = models.PositiveIntegerField(default=120)
    retention_hours = models.PositiveIntegerField(default=72)
    logo_path = models.CharField(max_length=500, blank=True)
    background_path = models.CharField(max_length=500, blank=True)
    ai_provider = models.CharField(max_length=40, default="disabled")
    ai_api_key_encrypted = models.TextField(blank=True)
    replicate_model_owner = models.CharField(max_length=120, default="cuuupid")
    replicate_model_name = models.CharField(max_length=120, default="idm-vton")
    replicate_model_version = models.CharField(max_length=180, blank=True)
    fashn_model_name = models.CharField(max_length=120, default="tryon-v1.6")
    camera_status = models.CharField(max_length=24, default="offline")
    camera_updated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "event settings"

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> EventSettings:
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    def __str__(self) -> str:
        return f"{self.event_title} — {self.event_date:%d %B %Y}"


class GuestSession(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    consented_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_reason = models.CharField(max_length=32, blank=True)

    def __str__(self) -> str:
        return f"Guest {str(self.pk)[:8]} ({self.status})"


class Gown(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=150, unique=True)
    name = models.CharField(max_length=140)
    color = models.CharField(max_length=80)
    description = models.TextField()
    image_path = models.CharField(max_length=500)
    image_mime = models.CharField(max_length=50, default="image/png")
    # Optional transparent cap/mortarboard. Try-on models only dress the body, so the cap is
    # placed on the guest's head after generation, from the detected face position.
    cap_image_path = models.CharField(max_length=500, blank=True)
    is_enabled = models.BooleanField(default=True, db_index=True)
    is_sample = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False, db_index=True)
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_order", "name")

    def __str__(self) -> str:
        return self.name


class CapturedPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest_session = models.ForeignKey(
        GuestSession,
        on_delete=models.CASCADE,
        related_name="captured_photos",
    )
    storage_path = models.CharField(max_length=500, blank=True)
    mime_type = models.CharField(max_length=50, default="image/jpeg")
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    file_size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self) -> str:
        return f"Capture {str(self.pk)[:8]}"


class GeneratedPortrait(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest_session = models.ForeignKey(
        GuestSession,
        on_delete=models.CASCADE,
        related_name="portraits",
    )
    photo = models.ForeignKey(CapturedPhoto, on_delete=models.PROTECT, related_name="portraits")
    gown = models.ForeignKey(Gown, on_delete=models.PROTECT, related_name="portraits")
    idempotency_key = models.CharField(max_length=180)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED, db_index=True)
    storage_path = models.CharField(max_length=500, blank=True)
    mime_type = models.CharField(max_length=50, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    provider_request_id = models.CharField(max_length=180, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("guest_session", "idempotency_key"),
                name="unique_guest_portrait_request",
            )
        ]
        indexes = [models.Index(fields=("status", "created_at"), name="portrait_status_created")]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Portrait {str(self.pk)[:8]} ({self.status})"


class GenerationJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    portrait = models.OneToOneField(
        GeneratedPortrait,
        on_delete=models.CASCADE,
        related_name="job",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    provider = models.CharField(max_length=40, blank=True)
    provider_request_id = models.CharField(max_length=180, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=("status", "available_at"), name="job_status_available")]
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"Job {str(self.pk)[:8]} ({self.status})"
