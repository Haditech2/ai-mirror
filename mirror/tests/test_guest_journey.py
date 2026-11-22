from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.utils.html import escape
from PIL import Image

from mirror.ai.base import AIResult
from mirror.models import EventSettings, GeneratedPortrait, Gown
from mirror.storage import storage


def sample_photo(color=(76, 98, 125)) -> bytes:
    image = Image.new("RGB", (900, 1200), color)
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=90)
    return stream.getvalue()


class GuestJourneyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.storage_root = Path(tempfile.mkdtemp(prefix="academic-mirror-tests-"))
        self.settings_override = override_settings(
            PRIVATE_STORAGE_ROOT=self.storage_root,
            GENERATION_INLINE=True,
            AI_PROVIDER="disabled",
            AI_API_KEY="",
        )
        self.settings_override.enable()
        for gown in Gown.objects.all():
            path = self.storage_root / gown.image_path
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (700, 1000), (25, 40, 70, 255)).save(path, "PNG")
        self.client = Client()

    def tearDown(self) -> None:
        self.settings_override.disable()
        shutil.rmtree(self.storage_root, ignore_errors=True)

    def _capture_and_select(self, photo: bytes | None = None) -> Gown:
        response = self.client.post("/consent/", {"consent": "on"})
        self.assertRedirects(response, "/camera/", fetch_redirect_response=False)
        upload = SimpleUploadedFile("capture.jpg", photo or sample_photo(), content_type="image/jpeg")
        response = self.client.post("/camera/", {"photo": upload})
        self.assertRedirects(response, "/collection/", fetch_redirect_response=False)
        gown = Gown.objects.filter(is_enabled=True, is_archived=False).first()
        response = self.client.post("/collection/", {"gown": str(gown.pk)})
        self.assertRedirects(response, "/fitting/", fetch_redirect_response=False)
        return gown

    def test_server_rendered_journey_fails_honestly_when_ai_is_disabled(self) -> None:
        welcome = self.client.get("/")
        self.assertEqual(welcome.status_code, 200)
        self.assertContains(welcome, "Professor Rashidah Funke Olarenwaju")
        self.assertEqual(self.client.get("/api/event").status_code, 404)
        self.assertEqual(self.client.post("/consent/", {}).status_code, 422)

        gown = self._capture_and_select()
        self.assertContains(self.client.get("/fitting/"), escape(gown.name))
        response = self.client.post("/portrait/generate/")
        self.assertEqual(response.status_code, 302)
        status = self.client.get(response.url)
        self.assertEqual(status.status_code, 200)
        self.assertContains(status, "This portrait could not be created")
        portrait = GeneratedPortrait.objects.latest("created_at")
        self.assertEqual(portrait.status, GeneratedPortrait.Status.FAILED)
        self.assertEqual(portrait.error_code, "provider_unavailable")
        self.assertFalse(portrait.storage_path)

    @override_settings(OPENAI_API_KEY="sk-test")
    def test_fitting_enables_generation_for_configured_openai(self) -> None:
        EventSettings.objects.update(ai_provider="openai")
        self._capture_and_select()
        response = self.client.get("/fitting/")
        self.assertTrue(response.context["ai_available"])
        self.assertNotContains(response, "Portraits are paused")

    def test_successful_provider_creates_signed_private_download(self) -> None:
        class ContractProvider:
            name = "contract-test"

            def generate(self, request):
                self_test.assertTrue(request.person_image.exists())
                self_test.assertTrue(request.garment_image.exists())
                self_test.assertEqual(request.category, "dresses")
                return AIResult(
                    image_bytes=sample_photo((100, 80, 60)),
                    request_id="provider-contract-1",
                    provider=self.name,
                )

        self_test = self
        self._capture_and_select()
        with patch("mirror.services.jobs.build_provider", return_value=ContractProvider()):
            response = self.client.post("/portrait/generate/")
        result_redirect = self.client.get(response.url)
        self.assertEqual(result_redirect.status_code, 302)
        result = self.client.get(result_redirect.url)
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Save it to your phone")
        self.assertContains(result, "Save portrait")
        self.assertContains(result, "Delete portrait")

        portrait = GeneratedPortrait.objects.latest("created_at")
        self.assertEqual(portrait.status, GeneratedPortrait.Status.COMPLETED)
        token_start = result.content.decode().find("token=")
        self.assertGreater(token_start, 0)
        token = result.content.decode()[token_start + 6 :].split("&", 1)[0].split('"', 1)[0]
        download = self.client.get(f"/portrait/{portrait.pk}/file/?token={token}")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "image/jpeg")
        download.close()
        self.assertEqual(self.client.get(f"/portrait/{portrait.pk}/file/?token=bad").status_code, 404)

        delete_url = f"/portrait/{portrait.pk}/delete/"
        image_path = self.storage_root / portrait.storage_path
        self.assertContains(self.client.get(delete_url), "Delete this portrait?")
        self.assertTrue(image_path.exists())
        other_guest = Client()
        other_guest.post("/consent/", {"consent": "on"})
        self.assertEqual(other_guest.post(delete_url).status_code, 404)
        self.assertTrue(image_path.exists())
        self.assertRedirects(self.client.post(delete_url), "/collection/", fetch_redirect_response=False)
        portrait.refresh_from_db()
        self.assertEqual(portrait.status, GeneratedPortrait.Status.DELETED)
        self.assertFalse(image_path.exists())
        self.assertEqual(self.client.get(f"/portrait/{portrait.pk}/file/?token={token}").status_code, 404)

    @override_settings(DATABASE_IMAGE_STORAGE=True)
    def test_database_storage_supports_capture_generation_and_download(self) -> None:
        gown = Gown.objects.filter(is_enabled=True, is_archived=False).first()
        storage.write(gown.image_path, sample_photo())
        self._capture_and_select()

        class ContractProvider:
            name = "openai"

            def generate(self, request):
                assert request.person_image.is_file() and request.garment_image.is_file()
                return AIResult(sample_photo(), "db-storage-test", self.name)

        with patch("mirror.services.jobs.build_provider", return_value=ContractProvider()):
            response = self.client.post("/portrait/generate/", follow=True)
        self.assertContains(response, "Save portrait")
        portrait = GeneratedPortrait.objects.latest("created_at")
        path = portrait.storage_path
        self.assertTrue(storage.exists(path))
        self.assertFalse((self.storage_root / path).exists())
        token = response.content.decode().split("token=", 1)[1].split('"', 1)[0]
        download = self.client.get(f"/portrait/{portrait.pk}/file/?token={token}")
        self.assertEqual(download.status_code, 200)
        download.close()
        self.client.post(f"/portrait/{portrait.pk}/delete/")
        self.assertFalse(storage.exists(path))

    def test_selected_gown_portrait_keeps_the_guests_own_face(self) -> None:
        from mirror.ai.face_lock import detect_faces
        from mirror.tests.test_face_lock import fake_try_on, standing_back_photo

        captured = standing_back_photo()
        original = Image.open(io.BytesIO(captured)).convert("RGB")

        class FaceChangingProvider:
            name = "face-changing"

            def generate(self, request):
                return AIResult(fake_try_on(original), "face-test-1", self.name)

        self._capture_and_select(captured)
        with patch("mirror.services.jobs.build_provider", return_value=FaceChangingProvider()):
            self.client.post("/portrait/generate/")

        portrait = GeneratedPortrait.objects.latest("created_at")
        self.assertEqual(portrait.status, GeneratedPortrait.Status.COMPLETED)
        result = Image.open(self.storage_root / portrait.storage_path).convert("RGB")
        self.assertEqual(result.size, original.size)
        face = detect_faces(np.asarray(original))[0]
        box = (
            int(face.x + face.w * 0.25),
            int(face.y + face.h * 0.25),
            int(face.x + face.w * 0.75),
            int(face.y + face.h * 0.75),
        )
        difference = np.abs(
            np.asarray(result.crop(box)).astype(int) - np.asarray(original.crop(box)).astype(int)
        ).mean()
        self.assertLess(difference, 4)

    def test_close_up_photo_is_turned_away_before_any_credit_is_spent(self) -> None:
        from mirror.ai.face_lock import FaceBox
        from mirror.models import CapturedPhoto

        self.client.post("/consent/", {"consent": "on"})
        upload = SimpleUploadedFile("close.jpg", sample_photo(), content_type="image/jpeg")
        with patch("mirror.ai.framing.detect_faces", return_value=[FaceBox(200, 200, 450, 600)]):
            response = self.client.post("/camera/", {"photo": upload})
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "head, shoulders, and chest", status_code=422)
        self.assertFalse(CapturedPhoto.objects.exists())
        self.assertFalse(any((self.storage_root / "photos").glob("*")))

    def test_gown_with_cap_puts_cap_on_head_and_keeps_face(self) -> None:
        from mirror.ai.face_lock import detect_faces
        from mirror.tests.test_face_lock import BUNDLED_CAP, fake_try_on, standing_back_photo

        for slug in ("royal-blue-graduation-gown-cap", "gold-doctoral-gown-tam"):
            seeded = Gown.objects.get(slug=slug)
            self.assertTrue(seeded.is_enabled and seeded.cap_image_path, slug)
        gown = Gown.objects.get(slug="royal-blue-graduation-gown-cap")
        shutil.copyfile(BUNDLED_CAP, self.storage_root / gown.cap_image_path)
        captured = standing_back_photo()
        original = Image.open(io.BytesIO(captured)).convert("RGB")

        class FaceChangingProvider:
            name = "face-changing"

            def generate(self, request):
                return AIResult(fake_try_on(original), "cap-test-1", self.name)

        self.client.post("/consent/", {"consent": "on"})
        self.client.post(
            "/camera/", {"photo": SimpleUploadedFile("c.jpg", captured, content_type="image/jpeg")}
        )
        collection = self.client.get("/collection/")
        self.assertContains(collection, f"/gowns/{gown.pk}/cap/")
        self.client.post("/collection/", {"gown": str(gown.pk)})
        with patch("mirror.services.jobs.build_provider", return_value=FaceChangingProvider()):
            self.client.post("/portrait/generate/")

        portrait = GeneratedPortrait.objects.latest("created_at")
        self.assertEqual(portrait.status, GeneratedPortrait.Status.COMPLETED)
        result = np.asarray(Image.open(self.storage_root / portrait.storage_path).convert("RGB")).astype(int)
        source = np.asarray(original).astype(int)
        face = detect_faces(np.asarray(original))[0]
        trim = source.shape[0] - result.shape[0]
        self.assertEqual(result.shape[1], source.shape[1])
        self.assertGreaterEqual(trim, 0)
        # The higher crown covers a substantial part of the region above the forehead.
        above = result[
            max(0, int(face.y - face.h * 0.5) - trim) : int(face.y) - trim,
            int(face.x) : int(face.x + face.w),
        ]
        blue = (above[..., 2] > 150) & (above[..., 2] > above[..., 0] + 60)
        self.assertGreater(blue.mean(), 0.25)
        # The guest's own face from the eyes down.
        eyes_down = (
            slice(int(face.y + face.h * 0.35) - trim, int(face.y + face.h * 0.8) - trim),
            slice(int(face.x + face.w * 0.25), int(face.x + face.w * 0.75)),
        )
        self.assertLess(np.abs(result[eyes_down] - source[trim:][eyes_down]).mean(), 4)
        self.assertEqual(self.client.get(f"/gowns/{gown.pk}/cap/").status_code, 200)


class BrowserFormPostTests(TransactionTestCase):
    """Browsers attach an Origin header to form POSTs; Django's test client does not by default."""

    def test_referrer_policy_lets_browsers_send_their_origin(self) -> None:
        # With "no-referrer" Chrome/Edge send `Origin: null` on POSTs and CSRF rejects every form.
        response = Client().get("/consent/")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_consent_post_with_browser_origin_passes_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        page = client.get("/consent/")
        token = page.content.decode().split('name="csrfmiddlewaretoken" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            "/consent/",
            {"consent": "on", "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="http://testserver",
        )
        self.assertRedirects(response, "/camera/", fetch_redirect_response=False)
        rejected = Client(enforce_csrf_checks=True)
        rejected.get("/consent/")
        response = rejected.post(
            "/consent/",
            {"consent": "on", "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="null",
        )
        self.assertEqual(response.status_code, 403)


class AdminConsoleTests(TransactionTestCase):
    def test_console_uses_django_session_authentication(self) -> None:
        client = Client()
        response = client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)
        login = client.post(
            "/admin/login/",
            {"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        self.assertRedirects(login, "/admin/", fetch_redirect_response=False)
        for path in (
            "/admin/",
            "/admin/gowns/",
            "/admin/portraits/",
            "/admin/settings/",
            "/admin/monitoring/",
        ):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_cap_upload_removes_white_background(self) -> None:
        client = Client()
        client.post(
            "/admin/login/", {"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD}
        )
        gown = Gown.objects.filter(is_sample=True).first()
        cap = Image.new("RGB", (600, 400), (255, 255, 255))
        cap.paste((30, 70, 200), (150, 120, 450, 300))
        stream = io.BytesIO()
        cap.save(stream, "JPEG", quality=95)
        storage_root = Path(tempfile.mkdtemp(prefix="academic-mirror-cap-"))
        try:
            with override_settings(PRIVATE_STORAGE_ROOT=storage_root):
                response = client.post(
                    f"/admin/gowns/{gown.pk}/edit/",
                    {
                        "name": gown.name,
                        "color": gown.color,
                        "description": gown.description,
                        "display_order": gown.display_order,
                        "is_enabled": "on",
                        "cap": SimpleUploadedFile("cap.jpg", stream.getvalue(), content_type="image/jpeg"),
                    },
                )
                self.assertEqual(response.status_code, 302, response.content[:500])
                gown.refresh_from_db()
                stored = Image.open(storage_root / gown.cap_image_path)
                self.assertEqual(stored.mode, "RGBA")
                self.assertEqual(stored.getpixel((5, 5))[3], 0)
                self.assertEqual(stored.getpixel((300, 200))[3], 255)
        finally:
            shutil.rmtree(storage_root, ignore_errors=True)
