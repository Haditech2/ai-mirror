from __future__ import annotations

import base64
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase
from PIL import Image

from mirror.ai.base import AIProviderError, AIResult, TryOnInput
from mirror.ai.face_lock import FaceBox
from mirror.ai.fashn import FashnTryOnProvider
from mirror.ai.openai import OpenAIImageProvider
from mirror.ai.replicate import ReplicateIDMVTONProvider

REAL_CLIENT = httpx.Client


def png_bytes(color=(90, 60, 40)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (300, 400), color).save(stream, "PNG")
    return stream.getvalue()


class OpenAIProviderTests(SimpleTestCase):
    def test_submits_photo_gown_cap_and_mask_in_one_edit(self) -> None:
        output = png_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://api.openai.com/v1/images/edits")
            self.assertEqual(request.headers["authorization"], "Bearer sk-test")
            self.assertEqual(request.content.count(b'name="image[]"'), 3)
            self.assertIn(b'name="mask"', request.content)
            return httpx.Response(
                200,
                headers={"x-request-id": "req_test"},
                json={"data": [{"b64_json": base64.b64encode(output).decode()}]},
            )

        with tempfile.TemporaryDirectory() as folder:
            photo, gown, cap = (Path(folder) / name for name in ("guest.png", "gown.png", "cap.png"))
            for path in (photo, gown, cap):
                path.write_bytes(output)
            request = TryOnInput(photo, gown, "Academic gown", cap_image=cap)
            client = REAL_CLIENT(transport=httpx.MockTransport(handler))
            with (
                patch("mirror.ai.openai.httpx.Client", return_value=client),
                patch("mirror.ai.openai.detect_faces", return_value=[FaceBox(90, 80, 120, 150)]),
            ):
                result = OpenAIImageProvider("sk-test", "gpt-image-2", 30).generate(request)

        self.assertEqual(result, AIResult(output, "req_test", "openai"))


class FashnProviderTests(SimpleTestCase):
    def test_connection_loss_during_polling_keeps_the_same_prediction(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                payload = json.loads(request.content)
                self.assertEqual(payload["model_name"], "tryon-max")
                self.assertEqual(
                    set(payload["inputs"]),
                    {"model_image", "product_image", "prompt", "resolution", "generation_mode"},
                )
                self.assertIn("chest-up crop", payload["inputs"]["prompt"])
                return httpx.Response(200, json={"id": "prediction-1"})
            if len(calls) == 2:
                raise httpx.ConnectError("getaddrinfo failed", request=request)
            if request.url.path == "/v1/status/prediction-1":
                return httpx.Response(200, json={"status": "completed", "output": ["https://example.com/out.png"]})
            return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})

        provider = FashnTryOnProvider("test-key", "https://api.fashn.ai/v1", "tryon-max", 30)
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "photo.png"
            photo.write_bytes(png_bytes())
            request = TryOnInput(photo, photo, "Academic gown", "dresses")
            client = REAL_CLIENT(transport=httpx.MockTransport(handler))
            with patch("mirror.ai.fashn.httpx.Client", return_value=client), patch("mirror.ai.fashn.time.sleep"):
                result = provider.generate(request)

        self.assertEqual(result.request_id, "prediction-1")
        self.assertEqual(
            calls,
            [
                ("POST", "/v1/run"),
                ("GET", "/v1/status/prediction-1"),
                ("GET", "/v1/status/prediction-1"),
                ("GET", "/out.png"),
            ],
        )


class ReplicateProviderTests(SimpleTestCase):
    """Runs the provider against a fake Replicate API (no network, no credits)."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="provider-tests-"))
        self.person = self.folder / "person.jpg"
        self.gown = self.folder / "gown.png"
        Image.new("RGB", (600, 800), (120, 100, 90)).save(self.person, "JPEG")
        Image.new("RGBA", (500, 700), (30, 60, 160, 255)).save(self.gown, "PNG")
        self.request = TryOnInput(self.person, self.gown, "Academic gown", "dresses")
        self.calls: list[tuple[str, str, dict | None]] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_against(self, handler, version: str | None = None):
        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return REAL_CLIENT(*args, **kwargs)

        provider = ReplicateIDMVTONProvider(
            api_key="r8_test",
            api_base_url="https://api.replicate.com/v1",
            owner="cuuupid",
            model="idm-vton",
            version=version,
            timeout_seconds=30,
        )
        with patch("mirror.ai.replicate.httpx.Client", side_effect=client_factory):
            return provider.generate(self.request)

    def fake_api(self, model_status: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            self.calls.append((request.method, request.url.path, body))
            path = request.url.path
            if request.method == "GET" and path == "/v1/models/cuuupid/idm-vton":
                if model_status != 200:
                    return httpx.Response(model_status, json={"detail": "nope"})
                return httpx.Response(200, json={"latest_version": {"id": "abc123version"}})
            if request.method == "POST" and path == "/v1/predictions":
                return httpx.Response(
                    201,
                    json={
                        "id": "pred-1",
                        "status": "succeeded",
                        "output": "https://replicate.delivery/out.png",
                    },
                )
            if request.method == "GET" and request.url.host == "replicate.delivery":
                return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
            return httpx.Response(404, json={"detail": f"unexpected {request.method} {path}"})

        return handler

    def test_blank_version_runs_the_models_latest_version(self) -> None:
        result = self.run_against(self.fake_api())

        self.assertEqual(result.request_id, "pred-1")
        self.assertTrue(result.image_bytes.startswith(b"\x89PNG"))
        post = next(call for call in self.calls if call[0] == "POST")
        self.assertEqual(post[1], "/v1/predictions")
        self.assertEqual(post[2]["version"], "abc123version")
        self.assertEqual(post[2]["input"]["category"], "dresses")
        self.assertTrue(post[2]["input"]["human_img"].startswith("data:image/jpeg;base64,"))

    def test_configured_version_skips_the_lookup(self) -> None:
        self.run_against(self.fake_api(), version="pinned999")

        self.assertFalse(any(call[1] == "/v1/models/cuuupid/idm-vton" for call in self.calls))
        post = next(call for call in self.calls if call[0] == "POST")
        self.assertEqual(post[2]["version"], "pinned999")

    def test_rejected_key_is_reported_without_retrying(self) -> None:
        with self.assertRaises(AIProviderError) as raised:
            self.run_against(self.fake_api(model_status=401))
        self.assertEqual(raised.exception.code, "provider_auth")
        self.assertFalse(raised.exception.retryable)
