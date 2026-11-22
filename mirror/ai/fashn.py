from __future__ import annotations

import time

import httpx

from .base import AIProviderError, AIResult, TryOnInput, VirtualTryOnProvider
from .common import download_result, image_data_uri, output_url

# Academic gowns are full-length; "auto" can treat them as tops and cut them at the waist.
FASHN_CATEGORIES = {"dresses": "one-pieces", "upper_body": "tops", "lower_body": "bottoms"}
CONNECTION_MESSAGE = "The portrait studio couldn't reach the image service. Please try again in a moment."


class FashnTryOnProvider(VirtualTryOnProvider):
    name = "fashn"

    def __init__(self, api_key: str, api_base_url: str, model_name: str, timeout_seconds: int) -> None:
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def generate(self, request: TryOnInput) -> AIResult:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        person = image_data_uri(request.person_image)
        garment = image_data_uri(request.garment_image)
        if self.model_name == "tryon-max":
            inputs = {
                "model_image": person,
                "product_image": garment,
                "prompt": (
                    "Replace the visible clothing below the neck with the academic gown from the product photo. "
                    "Keep the original chest-up crop and keep the head, shoulders, and neckline in their original "
                    "positions. Keep the shoulders and upper chest covered by the gown, with the collar at the "
                    "base of the neck. Preserve the face, hair, lighting, and background. Leave the head uncovered."
                ),
                "resolution": "1k",
                "generation_mode": "fast",
            }
        else:
            inputs = {
                "model_image": person,
                "garment_image": garment,
                "category": FASHN_CATEGORIES.get(request.category, "auto"),
                "garment_photo_type": "flat-lay",
                "num_samples": 1,
            }
        payload = {"model_name": self.model_name, "inputs": inputs}
        timeout = httpx.Timeout(connect=20, read=45, write=65, pool=20)
        with httpx.Client(timeout=timeout) as client:
            try:
                response = client.post(f"{self.api_base_url}/run", headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise AIProviderError(
                    f"FASHN request failed at {self.api_base_url}/run: {exc}",
                    code="provider_connection",
                    user_message=CONNECTION_MESSAGE,
                ) from exc
            if response.status_code in {401, 403}:
                raise AIProviderError(
                    "FASHN credential was rejected",
                    code="provider_auth",
                    retryable=False,
                    user_message="The AI studio needs attention from an event host. No portrait was created.",
                )
            if response.status_code == 429:
                raise AIProviderError(
                    "FASHN rate limit reached",
                    code="provider_busy",
                    user_message="The AI studio is very busy. Please wait a moment and try again.",
                )
            if response.is_error:
                raise AIProviderError(
                    f"FASHN returned {response.status_code}: {response.text[:500]}",
                    code="provider_rejected",
                )

            result = response.json()
            request_id = str(result.get("id")) if result.get("id") else None
            if not request_id:
                raise AIProviderError(
                    "FASHN job ID was missing",
                    code="invalid_provider_response",
                    retryable=False,
                )
            started = time.monotonic()
            last_poll_error = None
            while True:
                if time.monotonic() - started > self.timeout_seconds:
                    if last_poll_error is not None:
                        raise AIProviderError(
                            f"FASHN polling failed at {self.api_base_url}: {last_poll_error}",
                            code="provider_connection",
                            retryable=False,
                            user_message=CONNECTION_MESSAGE,
                            request_id=request_id,
                        ) from last_poll_error
                    raise AIProviderError(
                        "FASHN generation timed out",
                        code="provider_timeout",
                        request_id=request_id,
                    )
                time.sleep(2)
                try:
                    status_response = client.get(
                        f"{self.api_base_url}/status/{request_id}",
                        headers=headers,
                    )
                    status_response.raise_for_status()
                    status = status_response.json()
                except httpx.TransportError as exc:
                    # Keep polling the accepted prediction through a temporary network failure.
                    last_poll_error = exc
                    continue
                except httpx.HTTPError as exc:
                    raise AIProviderError(
                        f"FASHN polling failed: {exc}",
                        code="provider_connection",
                        request_id=request_id,
                    ) from exc
                last_poll_error = None
                state = str(status.get("status", "")).lower()
                if state in {"completed", "succeeded"}:
                    url = output_url(status.get("output") or status.get("outputs"))
                    if not url:
                        raise AIProviderError(
                            "FASHN output URL was missing",
                            code="invalid_provider_output",
                            retryable=False,
                            request_id=request_id,
                        )
                    return AIResult(
                        image_bytes=download_result(client, url, request_id),
                        request_id=request_id,
                        provider=self.name,
                    )
                if state in {"failed", "canceled", "cancelled"}:
                    error = status.get("error") or {}
                    name = error.get("name", "") if isinstance(error, dict) else str(error)
                    if "pose" in name.lower():
                        # No body found in the photo: retrying the same photo only spends credits.
                        raise AIProviderError(
                            f"FASHN could not find a body pose: {error}",
                            code="pose_not_found",
                            retryable=False,
                            user_message=(
                                "We couldn't detect your pose. Face the camera with both shoulders "
                                "and your chest visible, then retake the photo."
                            ),
                            request_id=request_id,
                        )
                    raise AIProviderError(
                        f"FASHN generation failed: {error or state}",
                        code="generation_failed",
                        request_id=request_id,
                    )
