"""Reject tight face crops before submitting a chest-up portrait for try-on."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .face_lock import detect_faces

# Allow tight chest-up photos and reject crops where the face fills about half the frame.
MAX_FACE_SHARE = 0.46

TOO_CLOSE_MESSAGE = (
    "Move back until your head, shoulders, and chest are visible, then take the photo again."
)


def too_close(photo_path: Path) -> bool:
    """True when the largest face takes up more than MAX_FACE_SHARE of the photo height.

    A photo where no face is found passes: the try-on service makes the final call.
    """
    with Image.open(photo_path) as image:
        rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
    faces = detect_faces(rgb)
    if not faces:
        return False
    tallest = max(face.h for face in faces)
    return tallest / rgb.shape[0] > MAX_FACE_SHARE
