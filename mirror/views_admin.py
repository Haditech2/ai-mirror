from __future__ import annotations

import io
import re
import time
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from PIL import Image, UnidentifiedImageError

from mirror.ai import provider_configuration
from mirror.forms import ConsoleAuthenticationForm, EventSettingsForm, GownForm
from mirror.models import CapturedPhoto, EventSettings, GeneratedPortrait, GenerationJob, Gown, GuestSession
from mirror.security import create_portrait_token, encrypt_secret
from mirror.storage import InvalidImageError, storage


def _staff(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_staff)


staff_required = user_passes_test(_staff, login_url=reverse_lazy("console:login"))


class ConsoleLoginView(LoginView):
    template_name = "mirror/admin/login.html"
    authentication_form = ConsoleAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self) -> str:
        target = self.get_redirect_url()
        return target or reverse("console:dashboard")


@require_POST
@login_required
def console_logout(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("console:login")


@require_GET
@staff_required
def dashboard(request: HttpRequest) -> HttpResponse:
    totals = {
        "guests_served": GuestSession.objects.count(),
        "photos_captured": CapturedPhoto.objects.count(),
        "portraits_generated": GeneratedPortrait.objects.filter(
            status=GeneratedPortrait.Status.COMPLETED
        ).count(),
        "failed_generations": GeneratedPortrait.objects.filter(
            status=GeneratedPortrait.Status.FAILED
        ).count(),
    }
    gown_rows = list(
        Gown.objects.filter(is_archived=False)
        .annotate(selection_count=Count("portraits"))
        .order_by("-selection_count", "display_order")
    )
    max_gown = max([item.selection_count for item in gown_rows] or [1]) or 1
    for item in gown_rows:
        item.selection_percent = item.selection_count / max_gown * 100

    recent = GeneratedPortrait.objects.select_related("gown").order_by("-created_at")[:12]
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    since = now - timedelta(hours=11)
    completed = list(
        GeneratedPortrait.objects.filter(
            status=GeneratedPortrait.Status.COMPLETED,
            completed_at__gte=since,
        ).values_list("completed_at", flat=True)
    )
    hourly = []
    counts = []
    for offset in range(11, -1, -1):
        hour = now - timedelta(hours=offset)
        count = sum(
            1 for value in completed if value and value.replace(minute=0, second=0, microsecond=0) == hour
        )
        counts.append(count)
        hourly.append({"label": hour.strftime("%H:%M"), "count": count})
    max_hour = max(counts or [1]) or 1
    for item in hourly:
        item["height"] = max(4, item["count"] / max_hour * 170)

    return render(
        request,
        "mirror/admin/dashboard.html",
        {
            "totals": totals,
            "gowns": gown_rows,
            "recent": recent,
            "hourly": hourly,
            "section": "dashboard",
        },
    )


@require_GET
@staff_required
def gowns(request: HttpRequest) -> HttpResponse:
    collection = Gown.objects.filter(is_archived=False).annotate(selection_count=Count("portraits"))
    return render(
        request,
        "mirror/admin/gowns.html",
        {"gowns": collection, "section": "gowns"},
    )


def _unique_slug(name: str, exclude=None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:130] or "gown"
    candidate = base
    suffix = 2
    while Gown.objects.filter(slug=candidate).exclude(pk=exclude).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@require_http_methods(["GET", "POST"])
@staff_required
def gown_create(request: HttpRequest) -> HttpResponse:
    return _gown_editor(request, None)


@require_http_methods(["GET", "POST"])
@staff_required
def gown_edit(request: HttpRequest, gown_id) -> HttpResponse:
    gown = get_object_or_404(Gown, pk=gown_id, is_archived=False)
    return _gown_editor(request, gown)


def _save_cap(upload):
    """Store a cap image as a transparent PNG, cutting out a plain white background if needed."""
    from mirror.ai.cap import cut_out_plain_background

    maximum = django_settings.MAX_UPLOAD_MB * 1024 * 1024
    data = upload.read(maximum + 1)
    if len(data) > maximum:
        raise InvalidImageError(f"Images must be smaller than {django_settings.MAX_UPLOAD_MB} MB.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("The file is not a valid PNG, JPEG or WebP image.") from exc
    if image.width * image.height > django_settings.MAX_IMAGE_PIXELS:
        raise InvalidImageError("The image dimensions are too large.")
    stream = io.BytesIO()
    cut_out_plain_background(image).save(stream, "PNG")
    return storage.save(stream.getvalue(), "gowns", preserve_alpha=True, max_edge=1600)


def _gown_editor(request: HttpRequest, gown: Gown | None) -> HttpResponse:
    form = GownForm(request.POST or None, request.FILES or None, instance=gown)
    if request.method == "POST" and form.is_valid():
        replacement = None
        upload = form.cleaned_data.get("image")
        if upload:
            try:
                replacement = storage.save_upload(
                    upload,
                    "gowns",
                    preserve_alpha=True,
                    max_edge=2400,
                )
            except InvalidImageError as exc:
                form.add_error("image", str(exc))
        new_cap = None
        cap_upload = form.cleaned_data.get("cap")
        if cap_upload and not form.errors:
            try:
                new_cap = _save_cap(cap_upload)
            except InvalidImageError as exc:
                form.add_error("cap", str(exc))
        if not form.errors:
            old_path = gown.image_path if gown and replacement else ""
            old_cap = gown.cap_image_path if gown and (new_cap or form.cleaned_data.get("remove_cap")) else ""
            item = form.save(commit=False)
            item.slug = _unique_slug(item.name, item.pk)
            if replacement:
                item.image_path = replacement.path
                item.image_mime = replacement.mime_type
                item.is_sample = False
            if new_cap:
                item.cap_image_path = new_cap.path
            elif form.cleaned_data.get("remove_cap"):
                item.cap_image_path = ""
            item.save()
            for stale in (old_path, old_cap):
                if stale and not Gown.objects.filter(Q(image_path=stale) | Q(cap_image_path=stale)).exists():
                    storage.delete(stale)
            messages.success(request, f"“{item.name}” was saved.")
            return redirect("console:gowns")
    return render(
        request,
        "mirror/admin/gown_form.html",
        {"form": form, "gown": gown, "section": "gowns"},
        status=422 if request.method == "POST" and form.errors else 200,
    )


@require_POST
@staff_required
def gown_archive(request: HttpRequest, gown_id) -> HttpResponse:
    gown = get_object_or_404(Gown, pk=gown_id, is_archived=False)
    gown.is_archived = True
    gown.is_enabled = False
    gown.save(update_fields=("is_archived", "is_enabled", "updated_at"))
    messages.success(request, f"“{gown.name}” was removed from the guest collection.")
    return redirect("console:gowns")


@require_GET
@staff_required
def portraits(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:120]
    records = GeneratedPortrait.objects.select_related("gown").filter(deleted_at__isnull=True)
    if query:
        records = records.filter(
            Q(id__icontains=query) | Q(status__icontains=query) | Q(gown__name__icontains=query)
        )
    items = []
    for portrait in records[:60]:
        inline_url = download_url = ""
        if portrait.status == GeneratedPortrait.Status.COMPLETED and portrait.storage_path:
            file_path = reverse("guest:portrait-file", kwargs={"portrait_id": portrait.pk})
            inline_url = f"{file_path}?token={create_portrait_token(portrait.pk, 'inline')}"
            download_url = f"{file_path}?token={create_portrait_token(portrait.pk, 'attachment')}"
        items.append({"portrait": portrait, "inline_url": inline_url, "download_url": download_url})
    return render(
        request,
        "mirror/admin/portraits.html",
        {"items": items, "query": query, "section": "portraits"},
    )


@require_POST
@staff_required
def portrait_delete(request: HttpRequest, portrait_id) -> HttpResponse:
    portrait = get_object_or_404(GeneratedPortrait, pk=portrait_id, deleted_at__isnull=True)
    if portrait.status in {GeneratedPortrait.Status.QUEUED, GeneratedPortrait.Status.PROCESSING}:
        messages.error(request, "A portrait cannot be deleted while it is being generated.")
    else:
        storage.delete(portrait.storage_path)
        portrait.storage_path = ""
        portrait.status = GeneratedPortrait.Status.DELETED
        portrait.deleted_at = timezone.now()
        portrait.save(update_fields=("storage_path", "status", "deleted_at", "updated_at"))
        messages.success(request, "The portrait file was permanently deleted.")
    return redirect("console:portraits")


@require_http_methods(["GET", "POST"])
@staff_required
def event_settings(request: HttpRequest) -> HttpResponse:
    event = EventSettings.load()
    form = EventSettingsForm(request.POST or None, request.FILES or None, instance=event)
    if request.method == "POST" and form.is_valid():
        pending_assets: dict[str, object] = {}
        for field, category, preserve in (("logo", "event", True), ("background", "event", False)):
            upload = form.cleaned_data.get(field)
            if upload:
                try:
                    pending_assets[field] = storage.save_upload(
                        upload,
                        category,
                        preserve_alpha=preserve,
                        max_edge=2560,
                    )
                except InvalidImageError as exc:
                    form.add_error(field, str(exc))
        if not form.errors:
            old_logo = event.logo_path if "logo" in pending_assets else ""
            old_background = event.background_path if "background" in pending_assets else ""
            event = form.save(commit=False)
            if form.cleaned_data.get("ai_api_key"):
                event.ai_api_key_encrypted = encrypt_secret(form.cleaned_data["ai_api_key"].strip())
            if "logo" in pending_assets:
                event.logo_path = pending_assets["logo"].path
            if "background" in pending_assets:
                event.background_path = pending_assets["background"].path
            event.save()
            storage.delete(old_logo)
            storage.delete(old_background)
            messages.success(request, "Event settings were saved.")
            return redirect("console:settings")
        for stored in pending_assets.values():
            storage.delete(stored.path)
    provider, key = provider_configuration(event)
    return render(
        request,
        "mirror/admin/settings.html",
        {
            "form": form,
            "event": event,
            "provider": provider,
            "api_key_configured": bool(key),
            "section": "settings",
        },
        status=422 if request.method == "POST" and form.errors else 200,
    )


@require_GET
@staff_required
def monitoring(request: HttpRequest) -> HttpResponse:
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    latency_ms = (time.perf_counter() - started) * 1000
    event = EventSettings.load()
    provider, key = provider_configuration(event)
    configured = provider in {"replicate", "fashn", "openai"} and bool(key)
    since = timezone.now() - timedelta(hours=1)
    recent_failures = GenerationJob.objects.filter(
        status=GenerationJob.Status.FAILED, created_at__gte=since
    ).count()
    ai_status = (
        "ready" if configured and not recent_failures else "degraded" if configured else "not configured"
    )
    ai_message = (
        "Configured and ready"
        if ai_status == "ready"
        else f"{recent_failures} recent failures"
        if configured
        else "Credential required"
    )
    camera_status = event.camera_status
    if not event.camera_updated_at or event.camera_updated_at < timezone.now() - timedelta(minutes=5):
        camera_status = "offline"
    camera_attention = camera_status in {"denied", "unavailable", "error"}
    used_bytes, file_count = storage.usage()
    queued = GenerationJob.objects.filter(status=GenerationJob.Status.QUEUED).count()
    processing = GenerationJob.objects.filter(status=GenerationJob.Status.PROCESSING).count()
    overall = "healthy" if configured and not recent_failures and not camera_attention else "attention"
    failures = GenerationJob.objects.filter(status=GenerationJob.Status.FAILED).order_by("-updated_at")[:20]
    return render(
        request,
        "mirror/admin/monitoring.html",
        {
            "overall": overall,
            "ai_status": ai_status,
            "ai_message": ai_message,
            "provider": provider,
            "database_latency": latency_ms,
            "used_bytes": used_bytes,
            "file_count": file_count,
            "camera_status": camera_status,
            "camera_updated_at": event.camera_updated_at,
            "queued": queued,
            "processing": processing,
            "workers": django_settings.GENERATION_WORKERS,
            "failures": failures,
            "section": "monitoring",
        },
    )
