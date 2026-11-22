"""Keep the guest's real face in every AI portrait.

Virtual try-on models re-synthesise the whole frame, so even when they are told to
"preserve the face" they can subtly change identity (eyes, skin, expression). This
module makes that impossible to ship: after the gown has been generated we find each
face in the guest's *original* photograph and blend those exact pixels back over the
AI output with a soft, feathered mask. A gentle exposure correction can lift deep
shadows in the finished portrait.
"""

from __future__ import annotations

import io
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .cap import load_cap, place_cap

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"
DETECT_MAX_EDGE = 1280
SCORE_THRESHOLD = 0.75
MIN_FACE_FRACTION = 0.04  # ignore detections narrower than 4% of the frame width
MAX_ALIGN_SHIFT = 0.3  # largest believable drift, as a fraction of face width

_local = threading.local()


@dataclass(frozen=True, slots=True)
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    right_eye: tuple[float, float] | None = None  # the subject's right eye (image left)
    left_eye: tuple[float, float] | None = None

    @property
    def roll(self) -> float:
        """Head tilt in degrees; positive when the head leans towards image-right."""
        if not self.right_eye or not self.left_eye:
            return 0.0
        dx = self.left_eye[0] - self.right_eye[0]
        dy = self.left_eye[1] - self.right_eye[1]
        return math.degrees(math.atan2(dy, dx)) if dx > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FaceLockResult:
    image_bytes: bytes
    faces_locked: int
    caps_placed: int = 0


def _detector(width: int, height: int) -> cv2.FaceDetectorYN:
    # FaceDetectorYN is not thread-safe; the portrait workers each get their own.
    detector = getattr(_local, "detector", None)
    if detector is None:
        detector = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (width, height), SCORE_THRESHOLD)
        _local.detector = detector
    detector.setInputSize((width, height))
    return detector


def detect_faces(rgb: np.ndarray) -> list[FaceBox]:
    height, width = rgb.shape[:2]
    scale = min(1.0, DETECT_MAX_EDGE / max(width, height))
    small = rgb
    if scale < 1.0:
        small = cv2.resize(rgb, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    _, found = _detector(bgr.shape[1], bgr.shape[0]).detect(bgr)
    if found is None:
        return []
    faces = []
    for row in found:
        x, y, w, h, rex, rey, lex, ley = (float(v) / scale for v in row[:8])
        if w >= width * MIN_FACE_FRACTION:
            faces.append(FaceBox(x, y, w, h, (rex, rey), (lex, ley)))
    return faces


def _face_mask(shape: tuple[int, int], face: FaceBox, offset: tuple[float, float]) -> np.ndarray:
    """Soft ellipse from the hairline to just under the chin, 1.0 in the core."""
    height, width = shape
    mask = np.zeros((height, width), dtype=np.float32)
    center = (round(face.x + face.w / 2 + offset[0]), round(face.y + face.h * 0.48 + offset[1]))
    axes = (max(1, round(face.w * 0.52)), max(1, round(face.h * 0.57)))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, thickness=-1)
    sigma = max(2.0, face.w * 0.035)
    # The blur is exactly 0.5 on the ellipse edge, so doubling it keeps every pixel inside
    # the ellipse fully original and places the soft transition just outside it.
    soft = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.clip(soft * 2.0, 0.0, 1.0)


def _drift(original: np.ndarray, generated: np.ndarray, face: FaceBox) -> tuple[float, float]:
    """How far the AI output moved the head, measured around the face."""
    height, width = original.shape[:2]
    pad_x, pad_y = face.w * 0.6, face.h * 0.6
    x0, y0 = max(0, int(face.x - pad_x)), max(0, int(face.y - pad_y))
    x1, y1 = min(width, int(face.x + face.w + pad_x)), min(height, int(face.y + face.h + pad_y))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return 0.0, 0.0
    a = cv2.cvtColor(original[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY).astype(np.float32)
    b = cv2.cvtColor(generated[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY).astype(np.float32)
    window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    if response < 0.05 or np.hypot(dx, dy) > face.w * MAX_ALIGN_SHIFT:
        return 0.0, 0.0
    # Whole pixels only: moving the face never resamples (softens) the guest's features.
    return float(round(dx)), float(round(dy))


def blend_faces(
    original: np.ndarray,
    generated: np.ndarray,
    faces: list[FaceBox],
    offsets: list[tuple[float, float]] | None = None,
) -> np.ndarray:
    """Return ``generated`` with each face region replaced by the original pixels."""
    height, width = original.shape[:2]
    if offsets is None:
        offsets = [_drift(original, generated, face) for face in faces]
    output = generated.astype(np.float32)
    for face, (dx, dy) in zip(faces, offsets, strict=True):
        source = original
        if dx or dy:
            shift = np.float32([[1, 0, dx], [0, 1, dy]])
            source = cv2.warpAffine(original, shift, (width, height), borderMode=cv2.BORDER_REFLECT)
        alpha = _face_mask((height, width), face, (dx, dy))[..., None]
        output = source.astype(np.float32) * alpha + output * (1.0 - alpha)
    return np.clip(output + 0.5, 0, 255).astype(np.uint8)


def _load_rgb(source: Path | bytes) -> Image.Image:
    image = Image.open(source if isinstance(source, Path) else io.BytesIO(source))
    return ImageOps.exif_transpose(image).convert("RGB")


def _align_single_face(generated: np.ndarray, original_face: FaceBox) -> np.ndarray | None:
    faces = detect_faces(generated)
    if len(faces) != 1:
        return None
    generated_face = faces[0]
    if not all((original_face.right_eye, original_face.left_eye, generated_face.right_eye, generated_face.left_eye)):
        return None
    original_eyes = np.asarray([original_face.right_eye, original_face.left_eye])
    generated_eyes = np.asarray([generated_face.right_eye, generated_face.left_eye])
    original_distance = np.linalg.norm(original_eyes[1] - original_eyes[0])
    generated_distance = np.linalg.norm(generated_eyes[1] - generated_eyes[0])
    if min(original_distance, generated_distance) < 1:
        return None
    scale = original_distance / generated_distance
    original_center = original_eyes.mean(axis=0)
    generated_center = generated_eyes.mean(axis=0)
    matrix = cv2.getRotationMatrix2D(tuple(generated_center), generated_face.roll - original_face.roll, scale)
    matrix[:, 2] += original_center - generated_center
    height, width = generated.shape[:2]
    return cv2.warpAffine(generated, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)


def _lift_portrait_shadows(portrait: np.ndarray, face: FaceBox) -> np.ndarray:
    """Lift deep shadows across the portrait while preserving highlights and skin hue."""
    height, width = portrait.shape[:2]
    x0 = max(0, round(face.x + face.w * 0.2))
    x1 = min(width, round(face.x + face.w * 0.8))
    y0 = max(0, round(face.y + face.h * 0.25))
    y1 = min(height, round(face.y + face.h * 0.75))
    sample = portrait[y0:y1, x0:x1]
    if not sample.size:
        return portrait
    brightness = np.percentile(cv2.cvtColor(sample, cv2.COLOR_RGB2GRAY), 70)
    strength = float(np.clip((90 - brightness) / 90, 0, 0.65))
    if strength == 0:
        return portrait
    rgb = portrait.astype(np.float32)
    peak = rgb.max(axis=2, keepdims=True) / 255.0
    return np.rint(rgb * (1 + strength * (1 - peak) ** 2)).astype(np.uint8)


def _blend_below_neck(original: np.ndarray, generated: np.ndarray, face: FaceBox) -> np.ndarray:
    """Keep the original head and background above the gown neckline."""
    height, width = original.shape[:2]
    chin = face.y + face.h
    columns = np.arange(width, dtype=np.float32)
    distance = np.abs(columns - (face.x + face.w / 2)) / (face.w / 2)
    # Protect the chin in the centre and give the gown room at the shoulders.
    start = chin - face.h * (0.065 + 0.095 * np.clip(distance, 0, 1))
    rows = np.arange(height, dtype=np.float32)[:, None]
    weight = np.clip((rows - start) / (face.h * 0.09), 0, 1)
    weight = (weight * weight * (3 - 2 * weight))[..., None]
    return np.rint(original * (1 - weight) + generated * weight).astype(np.uint8)


def lock_faces(original_path: Path, generated_bytes: bytes, cap_path: Path | None = None) -> FaceLockResult:
    """Restore the guest's real face(s) from ``original_path`` onto the AI output.

    The AI output is resized back to the original frame (try-on models work at a fixed
    resolution and keep the subject in place), faces are found in the original photo and
    blended back with a feathered mask. When ``cap_path`` is given the gown's cap is then
    placed on each head. If no face is found the AI output is returned unchanged.
    """
    original_image = _load_rgb(original_path)
    original = np.asarray(original_image)
    faces = detect_faces(original)
    if not faces:
        logger.warning("Face lock: no face found in %s; keeping AI output as-is", original_path.name)
        return FaceLockResult(generated_bytes, 0)

    generated_image = _load_rgb(generated_bytes)
    if generated_image.size != original_image.size:
        generated_image = generated_image.resize(original_image.size, Image.Resampling.LANCZOS)
    generated = np.asarray(generated_image)
    aligned = _align_single_face(generated, faces[0]) if len(faces) == 1 else None
    if aligned is not None:
        offsets = [(0.0, 0.0)]
        merged = _blend_below_neck(original, aligned, faces[0])
    else:
        offsets = [_drift(original, generated, face) for face in faces]
        merged = blend_faces(original, generated, faces, offsets)
    if len(faces) == 1:
        face = faces[0]
        dx, dy = offsets[0]
        face = FaceBox(face.x + dx, face.y + dy, face.w, face.h)
        merged = _lift_portrait_shadows(merged, face)

    caps = 0
    if cap_path is not None:
        cap = load_cap(cap_path)
        for face, offset in zip(faces, offsets, strict=True):
            capped = place_cap(merged, cap, face, offset)
            if len(faces) == 1:
                # Trim only above the cap. Keep the full width and all of the gown.
                rows = np.flatnonzero(np.any(capped != merged, axis=(1, 2)))
                if rows.size:
                    height = capped.shape[0]
                    margin = round(height * 0.09)
                    trim = min(round(height * 0.25), max(0, int(rows[0]) - margin))
                    capped = capped[trim:]
            merged = capped
            caps += 1

    stream = io.BytesIO()
    Image.fromarray(merged).save(stream, format="JPEG", quality=95, subsampling=0)
    return FaceLockResult(stream.getvalue(), len(faces), caps)
