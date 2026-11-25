from __future__ import annotations

from django.conf import settings

from mirror.models import EventSettings
from mirror.security import decrypt_secret

from .base import AIProviderError, VirtualTryOnProvider
from .fashn import FashnTryOnProvider
from .replicate import ReplicateIDMVTONProvider


def provider_configuration(event: EventSettings) -> tuple[str, str | None]:
    provider = (event.ai_provider or settings.AI_PROVIDER or "disabled").lower()
    saved_key = decrypt_secret(event.ai_api_key_encrypted)
    if provider == "openai":
        return provider, (saved_key if saved_key and saved_key.startswith("sk-") else settings.OPENAI_API_KEY or None)
    key = saved_key if saved_key and not saved_key.startswith("sk-") else settings.AI_API_KEY or None
    return provider, key


def build_provider(event: EventSettings) -> VirtualTryOnProvider:
    provider, key = provider_configuration(event)
    if provider == "disabled" or not key:
        raise AIProviderError(
            "No AI provider and credential are configured",
            code="provider_unavailable",
            retryable=False,
            user_message=(
                "The AI portrait studio is temporarily unavailable. Please ask an event host for assistance."
            ),
        )
    if provider == "replicate":
        return ReplicateIDMVTONProvider(
            api_key=key,
            api_base_url=settings.REPLICATE_API_BASE_URL,
            owner=event.replicate_model_owner,
            model=event.replicate_model_name,
            version=event.replicate_model_version,
            timeout_seconds=settings.GENERATION_TIMEOUT_SECONDS,
        )
    if provider == "openai":
        from .openai import OpenAIImageProvider

        return OpenAIImageProvider(key, settings.OPENAI_IMAGE_MODEL, settings.GENERATION_TIMEOUT_SECONDS)
    if provider == "fashn":
        return FashnTryOnProvider(
            api_key=key,
            api_base_url=settings.FASHN_API_BASE_URL,
            model_name=event.fashn_model_name,
            timeout_seconds=settings.GENERATION_TIMEOUT_SECONDS,
        )
    raise AIProviderError(
        f"Unsupported provider: {provider}",
        code="provider_unsupported",
        retryable=False,
        user_message=(
            "The configured AI portrait service is not supported. Please ask an event host for assistance."
        ),
    )
