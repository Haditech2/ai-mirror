from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import httpx

from .base import AIProviderError


def image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def download_result(client: httpx.Client, url: str, request_id: str | None) -> bytes:
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AIProviderError(
            f"Provider output download failed: {exc}",
            code="output_download_failed",
            request_id=request_id,
        ) from exc
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise AIProviderError(
            "Provider returned a non-image output",
            code="invalid_provider_output",
            retryable=False,
            user_message="The AI service returned an invalid image. Please choose the gown and try again.",
            request_id=request_id,
        )
    if len(response.content) > 25 * 1024 * 1024:
        raise AIProviderError(
            "Provider output was too large",
            code="output_too_large",
            retryable=False,
            request_id=request_id,
        )
    return response.content


def output_url(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return output_url(value[0])
    if isinstance(value, dict):
        for key in ("url", "image", "output"):
            if key in value:
                found = output_url(value[key])
                if found:
                    return found
    return None
