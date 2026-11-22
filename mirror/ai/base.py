from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class AIProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = True,
        user_message: str = "The AI portrait service could not complete this image. Please try again.",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.user_message = user_message
        self.request_id = request_id


@dataclass(slots=True)
class TryOnInput:
    person_image: Path
    garment_image: Path
    garment_description: str
    category: str = "upper_body"
    cap_image: Path | None = None


@dataclass(slots=True)
class AIResult:
    image_bytes: bytes
    request_id: str | None
    provider: str


class VirtualTryOnProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, request: TryOnInput) -> AIResult:
        raise NotImplementedError
