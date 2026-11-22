from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import OperationalError, ProgrammingError, close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from mirror.ai import AIProviderError, TryOnInput, build_provider
from mirror.ai.face_lock import lock_faces
from mirror.models import CapturedPhoto, EventSettings, GeneratedPortrait, GenerationJob, Gown, GuestSession
from mirror.storage import InvalidImageError, storage

logger = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.RLock()
_enqueued: set[str] = set()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=settings.GENERATION_WORKERS,
                thread_name_prefix="portrait-worker",
            )
        return _executor


def enqueue_job(job_id: UUID | str) -> None:
    key = str(job_id)
    if settings.GENERATION_INLINE:
        process_job(key)
        return
    with _executor_lock:
        if key in _enqueued:
            return
        _enqueued.add(key)
    future = _get_executor().submit(process_job, key)
    future.add_done_callback(lambda completed, value=key: _job_finished(value, completed))


def _job_finished(job_id: str, future: Future) -> None:
    with _executor_lock:
        _enqueued.discard(job_id)
    exception = future.exception()
    if exception:
        logger.exception("Portrait worker failed for job %s", job_id, exc_info=exception)


def recover_job_if_stale(job: GenerationJob) -> GenerationJob:
    cutoff = timezone.now() - timedelta(seconds=settings.GENERATION_TIMEOUT_SECONDS + 90)
    if job.status == GenerationJob.Status.PROCESSING and job.locked_at and job.locked_at < cutoff:
        GenerationJob.objects.filter(pk=job.pk, status=GenerationJob.Status.PROCESSING).update(
            status=GenerationJob.Status.QUEUED,
            locked_at=None,
        )
        GeneratedPortrait.objects.filter(
            pk=job.portrait_id, status=GeneratedPortrait.Status.PROCESSING
        ).update(status=GeneratedPortrait.Status.QUEUED)
        job.refresh_from_db()
    return job


def recover_and_enqueue_pending() -> None:
    """Recover durable jobs after a one-process application restart."""
    close_old_connections()
    try:
        processing_portraits = list(
            GenerationJob.objects.filter(status=GenerationJob.Status.PROCESSING).values_list(
                "portrait_id", flat=True
            )
        )
        GenerationJob.objects.filter(status=GenerationJob.Status.PROCESSING).update(
            status=GenerationJob.Status.QUEUED,
            locked_at=None,
        )
        if processing_portraits:
            GeneratedPortrait.objects.filter(pk__in=processing_portraits).update(
                status=GeneratedPortrait.Status.QUEUED
            )
        for job_id in GenerationJob.objects.filter(status=GenerationJob.Status.QUEUED).values_list(
            "pk", flat=True
        ):
            enqueue_job(job_id)
    except (OperationalError, ProgrammingError):
        # The first migration may not have created the queue tables yet.
        return
    finally:
        close_old_connections()


def queue_position(job: GenerationJob) -> int | None:
    if job.status != GenerationJob.Status.QUEUED:
        return None
    return GenerationJob.objects.filter(
        status=GenerationJob.Status.QUEUED,
        created_at__lte=job.created_at,
    ).count()


def process_job(job_id: UUID | str) -> None:
    close_old_connections()
    try:
        while True:
            job = _claim_job(job_id)
            if not job:
                return
            wait_seconds = max(0.0, (job.available_at - timezone.now()).total_seconds())
            if wait_seconds:
                time.sleep(min(wait_seconds, 30))
            try:
                provider = build_provider(EventSettings.load())
                job.provider = provider.name
                job.portrait.provider = provider.name
                job.save(update_fields=("provider", "updated_at"))
                job.portrait.save(update_fields=("provider", "updated_at"))

                photo_path = job.portrait.photo.storage_path
                if not photo_path:
                    raise AIProviderError(
                        "Source image was removed before processing",
                        code="source_missing",
                        retryable=False,
                        user_message="The source photograph is no longer available. Please take another photo.",
                    )
                gown = job.portrait.gown
                with ExitStack() as files:
                    cap = None
                    if gown.cap_image_path and storage.exists(gown.cap_image_path):
                        cap = files.enter_context(storage.local_path(gown.cap_image_path))
                    request = TryOnInput(
                        person_image=files.enter_context(storage.local_path(photo_path)),
                        garment_image=files.enter_context(storage.local_path(gown.image_path)),
                        garment_description=(
                            f"Academic ceremonial gown: {gown.name}. {gown.description}. "
                            "Preserve the person's face, natural skin tone, hands, pose, body proportions "
                            "and original background. Realistic fabric, fit, folds, lighting and shadows."
                        ),
                        category="dresses",
                        cap_image=cap,
                    )
                    result = provider.generate(request)
                    image_bytes = result.image_bytes
                    if result.provider != "openai":
                        image_bytes = _finish_portrait(request.person_image, image_bytes, cap, job.pk)
                stored = storage.save(
                    image_bytes,
                    "portraits",
                    preserve_alpha=False,
                    max_edge=2400,
                )
            except AIProviderError as exc:
                if _record_error(job.pk, exc):
                    continue
                return
            except InvalidImageError as exc:
                _record_error(
                    job.pk,
                    AIProviderError(
                        f"Provider image validation failed: {exc}",
                        code="invalid_provider_output",
                        retryable=False,
                        user_message="The AI service returned an invalid image. Please try the portrait again.",
                    ),
                )
                return
            except Exception as exc:
                logger.exception("Unexpected generation failure for job %s", job_id)
                _record_error(
                    job.pk,
                    AIProviderError(
                        f"Unexpected internal generation error: {exc}",
                        code="internal_generation_error",
                        retryable=False,
                        user_message=(
                            "The portrait could not be completed because of a system error. Please try again."
                        ),
                    ),
                )
                return
            _complete_job(job.pk, stored.path, stored.mime_type, result.provider, result.request_id)
            return
    finally:
        close_old_connections()


def _finish_portrait(person_image: Path, generated: bytes, cap_path: Path | None, job_id: UUID | str) -> bytes:
    """Put the guest's real face back on the AI output (so only the gown changes) and add the cap."""
    try:
        locked = lock_faces(person_image, generated, cap_path)
    except Exception:
        # Never lose a finished portrait over post-processing; the storage layer still
        # validates the provider image before it is saved.
        logger.exception("Face lock failed for job %s; keeping provider output", job_id)
        return generated
    logger.info(
        "Face lock restored %d face(s), placed %d cap(s) for job %s",
        locked.faces_locked,
        locked.caps_placed,
        job_id,
    )
    return locked.image_bytes


def _claim_job(job_id: UUID | str) -> GenerationJob | None:
    now = timezone.now()
    updated = GenerationJob.objects.filter(pk=job_id, status=GenerationJob.Status.QUEUED).update(
        status=GenerationJob.Status.PROCESSING,
        locked_at=now,
        started_at=now,
        attempts=F("attempts") + 1,
    )
    if not updated:
        return None
    job = GenerationJob.objects.select_related("portrait__photo", "portrait__gown").get(pk=job_id)
    GeneratedPortrait.objects.filter(pk=job.portrait_id).update(status=GeneratedPortrait.Status.PROCESSING)
    job.portrait.status = GeneratedPortrait.Status.PROCESSING
    return job


def _record_error(job_id: UUID | str, error: AIProviderError) -> bool:
    job = GenerationJob.objects.select_related("portrait").get(pk=job_id)
    retry = error.retryable and job.attempts < job.max_attempts
    job.provider_request_id = error.request_id or job.provider_request_id
    job.error_code = error.code
    job.error_message = str(error)[:4000]
    job.status = GenerationJob.Status.QUEUED if retry else GenerationJob.Status.FAILED
    job.locked_at = None
    if retry:
        job.available_at = timezone.now() + timedelta(seconds=min(12, 2**job.attempts))
    else:
        job.completed_at = timezone.now()
    job.save()

    portrait = job.portrait
    portrait.provider_request_id = error.request_id or portrait.provider_request_id
    portrait.error_code = error.code
    portrait.error_message = error.user_message
    portrait.status = GeneratedPortrait.Status.QUEUED if retry else GeneratedPortrait.Status.FAILED
    portrait.save()
    return retry


@transaction.atomic
def _complete_job(
    job_id: UUID | str,
    storage_path: str,
    mime_type: str,
    provider: str,
    request_id: str | None,
) -> None:
    now = timezone.now()
    job = GenerationJob.objects.select_for_update().select_related("portrait").get(pk=job_id)
    job.status = GenerationJob.Status.COMPLETED
    job.completed_at = now
    job.provider = provider
    job.provider_request_id = request_id or ""
    job.error_code = ""
    job.error_message = ""
    job.save()

    portrait = job.portrait
    portrait.status = GeneratedPortrait.Status.COMPLETED
    portrait.storage_path = storage_path
    portrait.mime_type = mime_type
    portrait.provider = provider
    portrait.provider_request_id = request_id or ""
    portrait.error_code = ""
    portrait.error_message = ""
    portrait.completed_at = now
    portrait.save()

    guest = GuestSession.objects.get(pk=portrait.guest_session_id)
    if guest.status != GuestSession.Status.ACTIVE:
        photo = CapturedPhoto.objects.get(pk=portrait.photo_id)
        if not photo.deleted_at:
            storage.delete(photo.storage_path)
            photo.storage_path = ""
            photo.deleted_at = now
            photo.save(update_fields=("storage_path", "deleted_at"))
