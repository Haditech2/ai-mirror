from __future__ import annotations

import base64
import io
import logging
from urllib.parse import urljoin

import qrcode
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from mirror.ai import provider_configuration
from mirror.ai.framing import TOO_CLOSE_MESSAGE, too_close
from mirror.forms import CaptureForm, ConsentForm, GownSelectionForm
from mirror.models import CapturedPhoto, EventSettings, GeneratedPortrait, GenerationJob, Gown
from mirror.security import create_portrait_token, decode_portrait_token
from mirror.services.jobs import enqueue_job, queue_position, recover_job_if_stale
from mirror.services.sessions import (
    GUEST_SESSION_KEY,
    complete_guest_session,
    current_guest,
    guest_session_required,
    start_guest_session,
)
from mirror.storage import InvalidImageError, storage

logger = logging.getLogger(__name__)


def _event_context() -> dict[str, object]:
    event = EventSettings.load()
    provider, key = provider_configuration(event)
    return {
        "event": event,
        "ai_available": provider in {"replicate", "fashn", "openai"} and bool(key),
    }


def _absolute_url(request: HttpRequest, path: str) -> str:
    if settings.PUBLIC_BASE_URL:
        return urljoin(f"{settings.PUBLIC_BASE_URL}/", path.lstrip("/"))
    return request.build_absolute_uri(path)


def _qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@require_GET
@never_cache
def welcome(request: HttpRequest) -> HttpResponse:
    context = _event_context()
    context["active_guest"] = bool(current_guest(request, touch=False))
    return render(request, "mirror/kiosk/welcome.html", context)


@require_http_methods(["GET", "POST"])
@never_cache
def consent(request: HttpRequest) -> HttpResponse:
    form = ConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        start_guest_session(request)
        return redirect("guest:camera")
    context = _event_context()
    context["form"] = form
    return render(
        request, "mirror/kiosk/consent.html", context, status=422 if request.method == "POST" else 200
    )


@require_http_methods(["GET", "POST"])
@guest_session_required
@never_cache
def camera(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CaptureForm(request.POST, request.FILES)
        if form.is_valid():
            upload = form.cleaned_data["photo"]
            if upload.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                form.add_error("photo", "Only JPEG, PNG or WebP photographs are accepted.")
            else:
                try:
                    stored = storage.save_upload(
                        upload,
                        "photos",
                        preserve_alpha=False,
                        max_edge=2048,
                    )
                except InvalidImageError as exc:
                    form.add_error("photo", str(exc))
                else:
                    if _photo_too_close(stored.path):
                        storage.delete(stored.path)
                        form.add_error("photo", TOO_CLOSE_MESSAGE)
                        return _render_camera(request, form)
                    photo = CapturedPhoto.objects.create(
                        guest_session=request.guest_session,
                        storage_path=stored.path,
                        mime_type=stored.mime_type,
                        width=stored.width,
                        height=stored.height,
                        file_size=stored.size,
                    )
                    request.session["active_photo_id"] = str(photo.pk)
                    request.session.pop("selected_gown_id", None)
                    request.session.pop("active_portrait_id", None)
                    return redirect("guest:gowns")
    else:
        form = CaptureForm()
    return _render_camera(request, form)


def _render_camera(request: HttpRequest, form: CaptureForm) -> HttpResponse:
    context = _event_context()
    context["form"] = form
    return render(request, "mirror/kiosk/camera.html", context, status=422 if form.errors else 200)


def _photo_too_close(relative_path: str) -> bool:
    """Free check before any try-on credit is spent; never blocks a guest if it fails."""
    try:
        with storage.local_path(relative_path) as path:
            return too_close(path)
    except Exception:
        logger.exception("Framing check failed; accepting the photo")
        return False


@require_POST
@guest_session_required
def camera_heartbeat(request: HttpRequest) -> HttpResponse:
    status = request.POST.get("status", "error")
    if status not in {"requesting", "ready", "denied", "unavailable", "error"}:
        status = "error"
    EventSettings.objects.filter(pk=1).update(camera_status=status, camera_updated_at=timezone.now())
    return HttpResponse(status=204)


@require_GET
@guest_session_required
@never_cache
def capture_preview(request: HttpRequest, photo_id) -> FileResponse:
    photo = get_object_or_404(
        CapturedPhoto,
        pk=photo_id,
        guest_session=request.guest_session,
        deleted_at__isnull=True,
    )
    if not photo.storage_path:
        raise Http404("Photograph no longer available")
    response = FileResponse(_open_image(photo.storage_path), content_type=photo.mime_type)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@require_http_methods(["GET", "POST"])
@guest_session_required
@never_cache
def gowns(request: HttpRequest) -> HttpResponse:
    photo_id = request.session.get("active_photo_id")
    photo = CapturedPhoto.objects.filter(
        pk=photo_id,
        guest_session=request.guest_session,
        deleted_at__isnull=True,
    ).first()
    if not photo or not photo.storage_path:
        messages.info(request, "Take a photograph before choosing a gown.")
        return redirect("guest:camera")
    form = GownSelectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.session["selected_gown_id"] = str(form.cleaned_data["gown"].pk)
        request.session.pop("active_portrait_id", None)
        return redirect("guest:fitting")
    available = list(Gown.objects.filter(is_enabled=True, is_archived=False))
    selected_id = request.session.get("selected_gown_id")
    for index, item in enumerate(available):
        item.kiosk_selected = str(item.pk) == selected_id or (not selected_id and index == 0)
    context = _event_context()
    context.update(
        {
            "form": form,
            "gowns": available,
            "selected_id": selected_id,
        }
    )
    return render(request, "mirror/kiosk/gowns.html", context)


def _active_photo_and_gown(request: HttpRequest):
    photo = get_object_or_404(
        CapturedPhoto,
        pk=request.session.get("active_photo_id"),
        guest_session=request.guest_session,
        deleted_at__isnull=True,
    )
    gown = get_object_or_404(
        Gown,
        pk=request.session.get("selected_gown_id"),
        is_enabled=True,
        is_archived=False,
    )
    return photo, gown


@require_GET
@guest_session_required
@never_cache
def fitting(request: HttpRequest) -> HttpResponse:
    try:
        photo, gown = _active_photo_and_gown(request)
    except Http404:
        return redirect("guest:gowns")
    context = _event_context()
    context.update({"photo": photo, "gown": gown})
    return render(request, "mirror/kiosk/fitting.html", context)


@require_POST
@guest_session_required
def generate_portrait(request: HttpRequest) -> HttpResponse:
    photo, gown = _active_photo_and_gown(request)
    base_key = f"{photo.pk}:{gown.pk}"
    existing = (
        GeneratedPortrait.objects.select_related("job")
        .filter(guest_session=request.guest_session, idempotency_key=base_key)
        .first()
    )
    if existing and existing.status in {
        GeneratedPortrait.Status.QUEUED,
        GeneratedPortrait.Status.PROCESSING,
        GeneratedPortrait.Status.COMPLETED,
    }:
        request.session["active_portrait_id"] = str(existing.pk)
        return redirect("guest:portrait-status", portrait_id=existing.pk)

    key = base_key
    if existing and existing.status == GeneratedPortrait.Status.FAILED:
        retry_count = GeneratedPortrait.objects.filter(
            guest_session=request.guest_session,
            idempotency_key__startswith=f"{base_key}:retry:",
        ).count()
        key = f"{base_key}:retry:{retry_count + 1}"[:180]

    try:
        with transaction.atomic():
            portrait = GeneratedPortrait.objects.create(
                guest_session=request.guest_session,
                photo=photo,
                gown=gown,
                idempotency_key=key,
            )
            job = GenerationJob.objects.create(
                portrait=portrait,
                max_attempts=max(1, settings.GENERATION_MAX_RETRIES + 1),
            )
            transaction.on_commit(lambda: enqueue_job(job.pk))
    except IntegrityError:
        portrait = get_object_or_404(
            GeneratedPortrait,
            guest_session=request.guest_session,
            idempotency_key=key,
        )
    request.session["active_portrait_id"] = str(portrait.pk)
    return redirect("guest:portrait-status", portrait_id=portrait.pk)


@require_GET
@guest_session_required
@never_cache
def portrait_status(request: HttpRequest, portrait_id) -> HttpResponse:
    portrait = get_object_or_404(
        GeneratedPortrait.objects.select_related("gown", "job"),
        pk=portrait_id,
        guest_session=request.guest_session,
    )
    job = recover_job_if_stale(portrait.job)
    portrait.refresh_from_db()
    if portrait.status == GeneratedPortrait.Status.COMPLETED:
        return redirect("guest:portrait-result", portrait_id=portrait.pk)
    if job.status == GenerationJob.Status.QUEUED:
        enqueue_job(job.pk)
    context = _event_context()
    context.update(
        {
            "portrait": portrait,
            "job": job,
            "queue_position": queue_position(job),
            "refresh": portrait.status
            in {GeneratedPortrait.Status.QUEUED, GeneratedPortrait.Status.PROCESSING},
        }
    )
    return render(request, "mirror/kiosk/waiting.html", context)


@require_GET
@guest_session_required
@never_cache
def portrait_result(request: HttpRequest, portrait_id) -> HttpResponse:
    portrait = get_object_or_404(
        GeneratedPortrait.objects.select_related("gown"),
        pk=portrait_id,
        guest_session=request.guest_session,
        status=GeneratedPortrait.Status.COMPLETED,
        deleted_at__isnull=True,
    )
    inline_token = create_portrait_token(portrait.pk, "inline")
    download_token = create_portrait_token(portrait.pk, "attachment")
    inline_path = reverse("guest:portrait-file", kwargs={"portrait_id": portrait.pk})
    portrait_url = f"{inline_path}?token={inline_token}"
    download_url = _absolute_url(request, f"{inline_path}?token={download_token}")
    context = _event_context()
    context.update(
        {
            "portrait": portrait,
            "portrait_url": portrait_url,
            "download_url": download_url,
            "qr_data_uri": _qr_data_uri(download_url),
        }
    )
    return render(request, "mirror/kiosk/result.html", context)


@require_http_methods(["GET", "POST"])
@guest_session_required
@never_cache
def portrait_delete(request: HttpRequest, portrait_id) -> HttpResponse:
    portraits = GeneratedPortrait.objects.filter(
        guest_session=request.guest_session,
        status=GeneratedPortrait.Status.COMPLETED,
        deleted_at__isnull=True,
    )
    portrait = get_object_or_404(portraits, pk=portrait_id)
    context = {**_event_context(), "portrait": portrait}
    if request.method == "GET":
        return render(request, "mirror/kiosk/delete_portrait.html", context)

    try:
        if portrait.storage_path:
            storage.delete(portrait.storage_path, strict=True)
    except (OSError, ValueError):
        logger.exception("Could not delete portrait %s", portrait.pk)
        context["delete_error"] = "The portrait could not be deleted. Please try again."
        return render(request, "mirror/kiosk/delete_portrait.html", context, status=503)

    now = timezone.now()
    portraits.filter(pk=portrait.pk).update(
        storage_path="", status=GeneratedPortrait.Status.DELETED, deleted_at=now, updated_at=now
    )
    if request.session.get("active_portrait_id") == str(portrait.pk):
        request.session.pop("active_portrait_id", None)
    messages.success(request, "Your portrait was deleted.")
    return redirect("guest:gowns")


@require_GET
@never_cache
def portrait_file(request: HttpRequest, portrait_id) -> FileResponse:
    token = request.GET.get("token", "")
    try:
        payload = decode_portrait_token(token)
    except signing.BadSignature as exc:
        raise Http404("This private download link is invalid or has expired.") from exc
    if payload.get("portrait_id") != str(portrait_id):
        raise Http404("This private download link does not match the portrait.")
    portrait = get_object_or_404(
        GeneratedPortrait,
        pk=portrait_id,
        status=GeneratedPortrait.Status.COMPLETED,
        deleted_at__isnull=True,
    )
    if not portrait.storage_path:
        raise Http404("This portrait is no longer available.")
    disposition = "inline" if payload.get("disposition") == "inline" else "attachment"
    response = FileResponse(
        _open_image(portrait.storage_path),
        content_type=portrait.mime_type or "image/jpeg",
        as_attachment=disposition == "attachment",
        filename=f"academic-portrait-{str(portrait.pk)[:8]}.jpg",
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@require_POST
def end_session(request: HttpRequest) -> HttpResponse:
    reason = request.POST.get("reason", "completed")
    if reason not in {"completed", "inactive", "cancelled"}:
        reason = "cancelled"
    guest = current_guest(request, touch=False)
    if guest:
        complete_guest_session(guest, reason)
    request.session.pop(GUEST_SESSION_KEY, None)
    request.session.pop("active_photo_id", None)
    request.session.pop("selected_gown_id", None)
    request.session.pop("active_portrait_id", None)
    if reason == "completed":
        return redirect("guest:complete")
    return redirect("guest:welcome")


@require_GET
@never_cache
def complete(request: HttpRequest) -> HttpResponse:
    return render(request, "mirror/kiosk/complete.html", _event_context())


@require_GET
@cache_control(public=True, max_age=2_592_000, immutable=True)
def gown_image(request: HttpRequest, gown_id) -> FileResponse:
    gown = get_object_or_404(Gown, pk=gown_id, is_archived=False)
    return FileResponse(_open_image(gown.image_path), content_type=gown.image_mime)


@require_GET
@cache_control(public=True, max_age=86_400)
def gown_cap_image(request: HttpRequest, gown_id) -> FileResponse:
    gown = get_object_or_404(Gown, pk=gown_id, is_archived=False)
    if not gown.cap_image_path:
        raise Http404("This gown has no cap")
    return FileResponse(_open_image(gown.cap_image_path), content_type="image/png")


@require_GET
@cache_control(public=True, max_age=3600)
def event_asset(request: HttpRequest, kind: str) -> FileResponse:
    if kind not in {"logo", "background"}:
        raise Http404("Asset not found")
    event = EventSettings.load()
    relative = event.logo_path if kind == "logo" else event.background_path
    if not relative:
        raise Http404("Asset not found")
    content = _open_image(relative)
    return FileResponse(content, filename=relative.rsplit("/", 1)[-1])


def _open_image(relative_path: str):
    try:
        return storage.open(relative_path)
    except FileNotFoundError as exc:
        raise Http404("Image no longer available.") from exc
