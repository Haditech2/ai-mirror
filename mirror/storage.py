from __future__ import annotations

import io
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import Length
from PIL import Image, ImageOps, UnidentifiedImageError

from mirror.models import StoredImageBlob


class InvalidImageError(ValueError):
    pass


@dataclass(slots=True)
class StoredImage:
    path: str
    mime_type: str
    width: int
    height: int
    size: int


class ImageStorage:
    allowed_formats = {"JPEG", "PNG", "WEBP"}

    @property
    def root(self) -> Path:
        root = Path(settings.PRIVATE_STORAGE_ROOT).resolve()
        for folder in ("photos", "gowns", "portraits", "event"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        return root

    def absolute(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        if self.root not in target.parents and target != self.root:
            raise ValueError("Invalid storage path")
        return target

    def open(self, relative_path: str):
        if settings.DATABASE_IMAGE_STORAGE:
            content = StoredImageBlob.objects.filter(pk=relative_path).values_list("content", flat=True).first()
            if content is None:
                raise FileNotFoundError(relative_path)
            return io.BytesIO(content)
        return self.absolute(relative_path).open("rb")

    def exists(self, relative_path: str) -> bool:
        if settings.DATABASE_IMAGE_STORAGE:
            return StoredImageBlob.objects.filter(pk=relative_path).exists()
        return self.absolute(relative_path).is_file()

    @contextmanager
    def local_path(self, relative_path: str):
        if not settings.DATABASE_IMAGE_STORAGE:
            yield self.absolute(relative_path)
            return
        with TemporaryDirectory(prefix="academic-mirror-") as directory, self.open(relative_path) as source:
            path = Path(directory) / PurePosixPath(relative_path).name
            path.write_bytes(source.read())
            yield path

    def write(self, relative_path: str, content: bytes) -> None:
        if settings.DATABASE_IMAGE_STORAGE:
            StoredImageBlob.objects.update_or_create(pk=relative_path, defaults={"content": content})
            return
        destination = self.absolute(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.{uuid.uuid4()}.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _open_validated(self, data: bytes) -> Image.Image:
        if not data:
            raise InvalidImageError("The uploaded image is empty.")
        if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise InvalidImageError(f"Images must be smaller than {settings.MAX_UPLOAD_MB} MB.")
        try:
            probe = Image.open(io.BytesIO(data))
            if probe.format not in self.allowed_formats:
                raise InvalidImageError("Only PNG, JPEG and WebP images are accepted.")
            if probe.width * probe.height > settings.MAX_IMAGE_PIXELS:
                raise InvalidImageError("The image dimensions are too large.")
            if probe.width < 256 or probe.height < 256:
                raise InvalidImageError("The image must be at least 256 × 256 pixels.")
            probe.verify()
            image = Image.open(io.BytesIO(data))
            image.load()
        except InvalidImageError:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise InvalidImageError("The file is not a valid PNG, JPEG or WebP image.") from exc
        return ImageOps.exif_transpose(image)

    def save(
        self,
        data: bytes,
        category: str,
        *,
        preserve_alpha: bool = False,
        max_edge: int = 2400,
    ) -> StoredImage:
        if category not in {"photos", "gowns", "portraits", "event"}:
            raise ValueError("Unsupported storage category")
        image = self._open_validated(data)
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        if preserve_alpha:
            image = image.convert("RGBA")
            extension, mime = ".png", "image/png"
        else:
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, (7, 16, 31))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            extension, mime = ".jpg", "image/jpeg"

        relative = f"{category}/{uuid.uuid4()}{extension}"
        with io.BytesIO() as output:
            if preserve_alpha:
                image.save(output, format="PNG", optimize=True, compress_level=7)
            else:
                image.save(output, format="JPEG", quality=92, optimize=True, progressive=True)
            content = output.getvalue()
        self.write(relative, content)
        return StoredImage(relative, mime, image.width, image.height, len(content))

    def save_upload(self, upload, category: str, **kwargs) -> StoredImage:
        maximum = settings.MAX_UPLOAD_MB * 1024 * 1024
        data = upload.read(maximum + 1)
        if len(data) > maximum:
            raise InvalidImageError(f"Images must be smaller than {settings.MAX_UPLOAD_MB} MB.")
        return self.save(data, category, **kwargs)

    def delete(self, relative_path: str | None, *, strict: bool = False) -> None:
        if not relative_path:
            return
        if settings.DATABASE_IMAGE_STORAGE:
            StoredImageBlob.objects.filter(pk=relative_path).delete()
            return
        try:
            self.absolute(relative_path).unlink(missing_ok=True)
        except (OSError, ValueError):
            if strict:
                raise

    def usage(self) -> tuple[int, int]:
        if settings.DATABASE_IMAGE_STORAGE:
            stats = StoredImageBlob.objects.aggregate(total=Sum(Length("content")), count=Count("path"))
            return stats["total"] or 0, stats["count"]
        total = 0
        count = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    continue
        return total, count


storage = ImageStorage()
