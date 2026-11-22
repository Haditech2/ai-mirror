from __future__ import annotations

import base64
import io
import mimetypes
from contextlib import ExitStack
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageOps

from .base import AIProviderError, AIResult, TryOnInput, VirtualTryOnProvider
from .face_lock import detect_faces


class OpenAIImageProvider(VirtualTryOnProvider):
    name = "openai"

    def __init__(self, api_key: str, model_name: str, timeout_seconds: int) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def generate(self, request: TryOnInput) -> AIResult:
        paths = [request.person_image, request.garment_image]
        cap_instruction = "Keep the guest's head uncovered."
        if request.cap_image is not None and request.cap_image.is_file():
            paths.append(request.cap_image)
            cap_instruction = (
                "Fit the cap in reference image 3 naturally on top of the guest's head, with realistic contact "
                "shadows and the brim clear of the eyes. Match its shape, color, and tassel."
            )
        prompt = (
            "Edit image 1 as a realistic chest-up photograph of the same guest. "
            "Replace only the clothing with the academic gown in reference image 2. "
            "Match the gown's colors, trim, and visible emblems. Keep the collar at the base of the neck "
            "and cover the shoulders and chest. Preserve the guest's facial features, expression, skin tone, "
            "head position, lighting, background, and camera framing. "
            f"{cap_instruction} {request.garment_description}"
        )
        try:
            with ExitStack() as stack:
                files = [("image[]", self._file(stack, path)) for path in paths]
                if self.model_name == "gpt-image-2":
                    mask = self._make_mask(request.person_image, len(paths) == 3)
                    if mask is not None:
                        files.append(("mask", ("portrait-mask.png", mask, "image/png")))
                client = stack.enter_context(httpx.Client(timeout=self.timeout_seconds))
                response = client.post(
                    "https://api.openai.com/v1/images/edits",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"model": self.model_name, "prompt": prompt, "size": "1024x1536", "quality": "medium"},
                    files=files,
                )
        except httpx.RequestError as exc:
            raise AIProviderError(
                "OpenAI image edit request failed",
                code="provider_connection",
                retryable=isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)),
            ) from exc

        request_id = response.headers.get("x-request-id")
        if response.is_error:
            status = response.status_code
            code = {401: "provider_auth", 403: "provider_auth", 429: "provider_busy"}.get(
                status, "provider_rejected"
            )
            if status == 429:
                try:
                    error_code = response.json()["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    error_code = None
                if error_code in ("insufficient_quota", "billing_hard_limit_reached"):
                    code = "provider_quota"
            raise AIProviderError(
                f"OpenAI image edit returned HTTP {status} ({code})",
                code=code,
                retryable=status >= 500,
                request_id=request_id,
            )
        try:
            data = base64.b64decode(response.json()["data"][0]["b64_json"], validate=True)
            if not data:
                raise ValueError("Empty image")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(
                "OpenAI image edit returned no valid image",
                code="invalid_provider_output",
                retryable=False,
                request_id=request_id,
            ) from exc
        return AIResult(data, request_id, self.name)

    @staticmethod
    def _file(stack: ExitStack, path: Path):
        return path.name, stack.enter_context(path.open("rb")), mimetypes.guess_type(path.name)[0] or "image/jpeg"

    @staticmethod
    def _make_mask(path: Path, has_cap: bool) -> bytes | None:
        with Image.open(path) as source:
            photo = ImageOps.exif_transpose(source).convert("RGB")
        faces = detect_faces(np.asarray(photo))
        if len(faces) != 1:
            return None
        face = faces[0]
        mask = Image.new("RGBA", photo.size, (255, 255, 255, 255))
        shoulder_start = max(0, round(face.y + face.h * 0.97))
        mask.paste(0, (0, shoulder_start, photo.width, photo.height))
        if has_cap:
            cap_bottom = max(0, round(face.y - face.h * 0.04))
            mask.paste(0, (0, 0, photo.width, cap_bottom))
        with io.BytesIO() as encoded:
            mask.save(encoded, "PNG")
            return encoded.getvalue()
