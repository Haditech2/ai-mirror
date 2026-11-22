from __future__ import annotations

import time

import httpx

from .base import AIProviderError, AIResult, TryOnInput, VirtualTryOnProvider
from .common import download_result, image_data_uri, output_url


class ReplicateIDMVTONProvider(VirtualTryOnProvider):
    name = "replicate"

    def __init__(
        self,
        api_key: str,
        api_base_url: str,
        owner: str,
        model: str,
        version: str | None,
        timeout_seconds: int,
    ) -> None:
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.owner = owner
        self.model = model
        self.version = version or None
        self.timeout_seconds = timeout_seconds

    def _latest_version(self, client: httpx.Client, headers: dict[str, str]) -> str | None:
        try:
            response = client.get(f"{self.api_base_url}/models/{self.owner}/{self.model}", headers=headers)
        except httpx.HTTPError as exc:
            raise AIProviderError(
                f"Replicate model lookup failed: {exc}", code="provider_connection"
            ) from exc
        if response.status_code in {401, 403}:
            raise AIProviderError(
                "Replicate credential was rejected",
                code="provider_auth",
                retryable=False,
                user_message="The AI studio needs attention from an event host. No portrait was created.",
            )
        if response.status_code == 404:
            raise AIProviderError(
                f"Replicate model {self.owner}/{self.model} was not found",
                code="provider_model_missing",
                retryable=False,
                user_message="The AI studio needs attention from an event host. No portrait was created.",
            )
        if response.is_error:
            return None
        latest = (response.json() or {}).get("latest_version") or {}
        return latest.get("id") or None

    def generate(self, request: TryOnInput) -> AIResult:
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "wait=55",
        }
        inputs = {
            "human_img": image_data_uri(request.person_image),
            "garm_img": image_data_uri(request.garment_image),
            "garment_des": request.garment_description,
            "category": request.category,
            "crop": False,
            "steps": 30,
        }
        timeout = httpx.Timeout(connect=20, read=65, write=65, pool=20)
        with httpx.Client(timeout=timeout) as client:
            # Community models such as cuuupid/idm-vton can only be run by version; the
            # /models/.../predictions shortcut works for official models alone. With no version
            # configured, use the model's latest published version.
            version = self.version or self._latest_version(client, headers)
            if version:
                endpoint = f"{self.api_base_url}/predictions"
                payload = {"version": version, "input": inputs}
            else:
                endpoint = f"{self.api_base_url}/models/{self.owner}/{self.model}/predictions"
                payload = {"input": inputs}
            try:
                response = client.post(endpoint, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise AIProviderError(f"Replicate request failed: {exc}", code="provider_connection") from exc
            if response.status_code in {401, 403}:
                raise AIProviderError(
                    "Replicate credential was rejected",
                    code="provider_auth",
                    retryable=False,
                    user_message="The AI studio needs attention from an event host. No portrait was created.",
                )
            if response.status_code == 429:
                raise AIProviderError(
                    "Replicate rate limit reached",
                    code="provider_busy",
                    user_message="The AI studio is very busy. Please wait a moment and try again.",
                )
            if response.is_error:
                raise AIProviderError(
                    f"Replicate returned {response.status_code}: {response.text[:500]}",
                    code="provider_rejected",
                )

            prediction = response.json()
            request_id = str(prediction.get("id")) if prediction.get("id") else None
            started = time.monotonic()
            while prediction.get("status") not in {"succeeded", "failed", "canceled"}:
                if time.monotonic() - started > self.timeout_seconds:
                    raise AIProviderError(
                        "Replicate prediction timed out",
                        code="provider_timeout",
                        request_id=request_id,
                    )
                poll_url = prediction.get("urls", {}).get("get")
                if not poll_url:
                    raise AIProviderError(
                        "Replicate did not provide a polling URL",
                        code="invalid_provider_response",
                        retryable=False,
                        request_id=request_id,
                    )
                time.sleep(2)
                try:
                    poll = client.get(poll_url, headers=headers)
                    poll.raise_for_status()
                    prediction = poll.json()
                except httpx.HTTPError as exc:
                    raise AIProviderError(
                        f"Replicate polling failed: {exc}",
                        code="provider_connection",
                        request_id=request_id,
                    ) from exc

            if prediction.get("status") != "succeeded":
                detail = prediction.get("error") or prediction.get("status")
                raise AIProviderError(
                    f"Replicate generation failed: {detail}",
                    code="generation_failed",
                    request_id=request_id,
                )
            url = output_url(prediction.get("output"))
            if not url:
                raise AIProviderError(
                    "Replicate output URL was missing",
                    code="invalid_provider_output",
                    retryable=False,
                    request_id=request_id,
                )
            return AIResult(
                image_bytes=download_result(client, url, request_id),
                request_id=request_id,
                provider=self.name,
            )
