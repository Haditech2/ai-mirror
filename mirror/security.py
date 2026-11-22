from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core import signing


def _fernet() -> Fernet:
    if settings.SETTINGS_ENCRYPTION_KEY:
        key = settings.SETTINGS_ENCRYPTION_KEY.encode("ascii")
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def create_portrait_token(portrait_id: object, disposition: str = "attachment") -> str:
    return signing.dumps(
        {"portrait_id": str(portrait_id), "disposition": disposition},
        salt="academic-mirror-portrait",
        compress=True,
    )


def decode_portrait_token(token: str) -> dict[str, str]:
    return signing.loads(
        token,
        salt="academic-mirror-portrait",
        max_age=settings.DOWNLOAD_TOKEN_MINUTES * 60,
    )
