"""Place a graduation cap on the guest's head.

Virtual try-on models only dress the body, so a mortarboard in the garment image is either
ignored or smeared into the gown. Instead the cap is stored as its own transparent image and
composited onto the finished portrait using the face detector's box and eye landmarks. It
sits on the head above the face, so the guest's face itself is never touched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

# Where the crown band sits relative to the face box, and how wide it is.
CROWN_WIDTH_TO_FACE = 0.82
RIM_BELOW_FACE_TOP = -0.025
BRIM_TO_HEAD = 1.85


@dataclass(frozen=True, slots=True)
class CapAnchor:
    """Bottom-centre of the crown band (the part that grips the head), in cap pixels."""

    x: float
    y: float
    crown_width: float


def _longest_run(row: np.ndarray) -> tuple[int, int]:
    best, start, best_span = 0, -1, (0, 0)
    for index, filled in enumerate(np.append(row, False)):
        if filled and start < 0:
            start = index
        elif not filled and start >= 0:
            if index - start > best:
                best, best_span = index - start, (start, index)
            start = -1
    return best_span


def cap_anchor(cap: np.ndarray) -> CapAnchor:
    """Find where an RGBA cap image grips the head.

    The anchor is the bottom of the lowest solid band at least a fifth of the image wide,
    so thin parts such as a hanging tassel are ignored. The headband itself can hide
    behind a tam's brim in a silhouette, so its width is estimated from the widest part
    (mortarboard or brim), which is consistently about 1.85x the head on both styles.
    """
    solid = cap[..., 3] > 128
    height, width = solid.shape
    spans = [_longest_run(solid[y]) for y in range(height)]
    widths = [b - a for a, b in spans]
    wide = [y for y, size in enumerate(widths) if size >= width * 0.2]
    if not wide:
        return CapAnchor(width / 2, height, width / BRIM_TO_HEAD)
    bottom = wide[-1]
    band = spans[max(wide[0], bottom - 3)]
    return CapAnchor((band[0] + band[1]) / 2, float(bottom), max(widths) / BRIM_TO_HEAD)


def place_cap(portrait: np.ndarray, cap: np.ndarray, face, offset=(0.0, 0.0)) -> np.ndarray:
    """Composite an RGBA ``cap`` onto an RGB ``portrait`` for one detected face."""
    anchor = cap_anchor(cap)
    scale = face.w * CROWN_WIDTH_TO_FACE / max(anchor.crown_width, 1.0)
    roll = face.roll
    # Seat the rim just above the detected face box, following the head's tilt.
    radians = math.radians(roll)
    centre_x = face.x + face.w / 2 + offset[0]
    centre_y = face.y + face.h / 2 + offset[1]
    up = face.h * (0.5 - RIM_BELOW_FACE_TOP)
    target = (centre_x + up * math.sin(radians), centre_y - up * math.cos(radians))

    matrix = cv2.getRotationMatrix2D((anchor.x, anchor.y), -roll, scale)
    matrix[0, 2] += target[0] - anchor.x
    matrix[1, 2] += target[1] - anchor.y
    height, width = portrait.shape[:2]
    # Premultiply before interpolating so transparent pixels cannot darken the cap edges.
    premultiplied = cap.astype(np.float32)
    premultiplied[..., :3] *= premultiplied[..., 3:4] / 255.0
    warped = cv2.warpAffine(
        premultiplied,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    alpha = warped[..., 3:4] / 255.0
    merged = warped[..., :3] + portrait.astype(np.float32) * (1.0 - alpha)
    return np.clip(merged + 0.5, 0, 255).astype(np.uint8)


def load_cap(path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def cut_out_plain_background(image: Image.Image) -> Image.Image:
    """Make a near-white product-shot background transparent (no-op if already transparent)."""
    rgba = image.convert("RGBA")
    pixels = np.asarray(rgba).copy()
    if (pixels[..., 3] < 250).mean() > 0.01:
        return rgba
    height, width = pixels.shape[:2]
    fill = (pixels[..., :3].min(axis=2) > 232).astype(np.uint8)
    mask = np.zeros((height + 2, width + 2), np.uint8)
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if fill[y, x] == 1:
            cv2.floodFill(fill, mask, (x, y), 2)
    alpha = np.where(fill == 2, 0, 255).astype(np.uint8)
    alpha = cv2.GaussianBlur(cv2.erode(alpha, np.ones((2, 2), np.uint8)), (0, 0), 0.8)
    pixels[..., 3] = alpha
    return Image.fromarray(pixels, "RGBA")
