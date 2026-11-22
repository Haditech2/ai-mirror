from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.conf import settings
from django.test import SimpleTestCase
from PIL import Image

from mirror.ai.cap import cap_anchor, cut_out_plain_background, load_cap, place_cap
from mirror.ai.face_lock import FaceBox, _lift_portrait_shadows, blend_faces, detect_faces, lock_faces
from mirror.ai.framing import too_close

REAL_FACE = Path(settings.BASE_DIR) / "mirror" / "static" / "professor-rashidah-portrait.jpg"


def fake_try_on(original: Image.Image, size=(768, 1024)) -> bytes:
    """Mimic a try-on model: fixed output size, new clothes, and a *changed* face."""
    generated = np.asarray(original.resize(size, Image.Resampling.LANCZOS)).copy()
    generated[:] = (generated * 0.4 + np.array([20, 120, 40]) * 0.6).astype(np.uint8)
    stream = io.BytesIO()
    Image.fromarray(generated).save(stream, "JPEG", quality=92)
    return stream.getvalue()


def standing_back_photo() -> bytes:
    """The sample portrait placed small in a 2:3 frame, as if the guest stood back from the camera."""
    canvas = Image.new("RGB", (1200, 1800), (236, 231, 222))
    person = Image.open(REAL_FACE).convert("RGB")
    person = person.resize((600, round(person.height * 600 / person.width)), Image.Resampling.LANCZOS)
    canvas.paste(person, (300, 160))
    stream = io.BytesIO()
    canvas.save(stream, "JPEG", quality=92)
    return stream.getvalue()


class BlendFacesTests(SimpleTestCase):
    def test_face_region_comes_from_original_and_rest_from_ai(self) -> None:
        rng = np.random.default_rng(1)
        original = rng.integers(0, 255, (600, 400, 3), dtype=np.uint8)
        generated = np.full_like(original, (10, 200, 30))
        face = FaceBox(150, 100, 100, 130)

        merged = blend_faces(original, generated, [face])

        core = (slice(140, 220), slice(170, 230))
        self.assertTrue(np.array_equal(merged[core], original[core]))
        self.assertTrue(np.array_equal(merged[450:, :], generated[450:, :]))
        self.assertTrue(np.array_equal(merged[:, :40], generated[:, :40]))

    def test_follows_the_head_when_the_model_shifts_it(self) -> None:
        rng = np.random.default_rng(2)
        original = rng.integers(0, 255, (600, 400, 3), dtype=np.uint8)
        generated = np.roll(original, shift=(6, 9), axis=(0, 1))  # head moved 9px right, 6px down
        generated[400:] = (10, 200, 30)
        face = FaceBox(150, 100, 100, 130)

        merged = blend_faces(original, generated, [face])

        core = (slice(150, 210), slice(180, 220))
        shifted_original = np.roll(original, shift=(6, 9), axis=(0, 1))
        self.assertTrue(np.array_equal(merged[core], shifted_original[core]))


class LockFacesTests(SimpleTestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="face-lock-tests-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_shadow_correction_lifts_face_without_blowing_out_highlights(self) -> None:
        portrait = np.full((200, 200, 3), 245, dtype=np.uint8)
        portrait[40:140, 50:150] = (46, 35, 30)
        corrected = _lift_portrait_shadows(portrait, FaceBox(50, 40, 100, 100))
        self.assertTrue(np.all(corrected[90, 100] > portrait[90, 100]))
        np.testing.assert_array_equal(corrected[0, 0], portrait[0, 0])

    def test_shifted_generated_portrait_is_aligned_before_restoring_face(self) -> None:
        original = self.folder / "original.png"
        Image.new("RGB", (300, 400), (120, 100, 90)).save(original)
        generated = Image.new("RGB", (300, 400), (20, 100, 40))
        generated.paste((240, 200, 30), (120, 280, 160, 320))
        stream = io.BytesIO()
        generated.save(stream, "PNG")
        original_face = FaceBox(80, 60, 100, 130, (105, 110), (155, 110))
        shifted_face = FaceBox(100, 90, 100, 130, (125, 140), (175, 140))

        with patch("mirror.ai.face_lock.detect_faces", side_effect=[[original_face], [shifted_face]]):
            result = lock_faces(original, stream.getvalue())

        pixels = np.asarray(Image.open(io.BytesIO(result.image_bytes))).astype(int)
        # Preserve the original background beside the head as well as the face.
        self.assertLess(np.abs(pixels[130, 220] - (120, 100, 90)).max(), 5)
        self.assertLess(np.abs(pixels[270, 120] - (240, 200, 30)).max(), 5)
        self.assertLess(np.abs(pixels[300, 140] - (20, 100, 40)).max(), 5)

    def test_real_face_is_restored_pixel_for_pixel(self) -> None:
        original = Image.open(REAL_FACE).convert("RGB")
        self.assertEqual(len(detect_faces(np.asarray(original))), 1)

        result = lock_faces(REAL_FACE, fake_try_on(original))

        self.assertEqual(result.faces_locked, 1)
        merged = np.asarray(Image.open(io.BytesIO(result.image_bytes)).convert("RGB")).astype(int)
        source = np.asarray(original).astype(int)
        self.assertEqual(merged.shape, source.shape)
        face = detect_faces(np.asarray(original))[0]
        cx, cy = int(face.x + face.w / 2), int(face.y + face.h / 2)
        core = (
            slice(cy - int(face.h * 0.3), cy + int(face.h * 0.3)),
            slice(cx - int(face.w * 0.3), cx + int(face.w * 0.3)),
        )
        # JPEG re-encoding only: the face matches the guest's photo, not the altered AI face.
        self.assertLess(np.abs(merged[core] - source[core]).mean(), 3)
        # The clothing area below the face still comes from the AI output.
        body = (slice(int(face.y + face.h * 1.6), source.shape[0]), slice(0, source.shape[1]))
        self.assertGreater(np.abs(merged[body] - source[body]).mean(), 20)

    def test_photo_without_a_face_keeps_ai_output(self) -> None:
        plain = self.folder / "plain.jpg"
        Image.new("RGB", (900, 1200), (76, 98, 125)).save(plain, "JPEG")
        generated = fake_try_on(Image.open(plain))

        result = lock_faces(plain, generated)

        self.assertEqual(result.faces_locked, 0)
        self.assertEqual(result.image_bytes, generated)


BUNDLED_CAP = Path(settings.BASE_DIR) / "mirror" / "assets" / "gowns" / "royal-blue-graduation-cap.png"
BUNDLED_TAM = Path(settings.BASE_DIR) / "mirror" / "assets" / "gowns" / "gold-doctoral-cap.png"


class CapTests(SimpleTestCase):
    def test_anchor_finds_the_headband_not_the_tassel(self) -> None:
        for cap_file in (BUNDLED_CAP, BUNDLED_TAM):
            with self.subTest(cap=cap_file.name):
                cap = load_cap(cap_file)
                anchor = cap_anchor(cap)
                height, width = cap.shape[:2]
                # The tassel hangs on the right, so it must not pull the anchor to the right.
                self.assertLess(anchor.x, width * 0.52)
                self.assertTrue(0.35 * width < anchor.crown_width < 0.6 * width)
                # The anchor is the bottom of the headband, well above the tassel's end.
                self.assertTrue(0.45 * height < anchor.y < 0.75 * height)

    def test_cap_sits_on_the_head_and_leaves_the_face_alone(self) -> None:
        portrait = np.full((900, 700, 3), 90, dtype=np.uint8)
        face = FaceBox(250, 300, 200, 260, (300, 400), (400, 400))

        capped = place_cap(portrait, load_cap(BUNDLED_CAP), face)

        changed = np.abs(capped.astype(int) - portrait.astype(int)).sum(axis=2) > 30
        self.assertTrue(changed[: int(face.y)].any())  # the cap rises above the head
        # Below the brow, nothing over the face changes (the tassel hangs beside it).
        face_area = changed[int(face.y + face.h * 0.3) :, int(face.x) : int(face.x + face.w)]
        self.assertFalse(face_area.any())

    def test_cap_follows_head_tilt(self) -> None:
        portrait = np.full((900, 700, 3), 90, dtype=np.uint8)
        cap = load_cap(BUNDLED_CAP)

        def board_tilt(face):
            changed = np.abs(place_cap(portrait, cap, face).astype(int) - 90).sum(axis=2) > 30
            cols = np.where(changed.any(axis=0))[0]
            left, right = cols.min() + 5, cols.max() - 60  # skip the tassel side
            top = [np.where(changed[:, c])[0].min() for c in (left, right)]
            return top[1] - top[0]

        level = board_tilt(FaceBox(250, 300, 200, 260, (300, 400), (400, 400)))
        tilted = board_tilt(FaceBox(250, 300, 200, 260, (300, 380), (400, 420)))
        self.assertGreater(tilted, level + 20)

    def test_white_background_cap_is_cut_out(self) -> None:
        image = Image.new("RGB", (400, 300), (255, 255, 255))
        image.paste((30, 70, 200), (100, 80, 300, 200))
        cut = np.asarray(cut_out_plain_background(image))
        self.assertEqual(cut[5, 5, 3], 0)
        self.assertEqual(cut[140, 200, 3], 255)


class FramingTests(SimpleTestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="framing-tests-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_chest_up_photo_passes_the_local_framing_check(self) -> None:
        self.assertFalse(too_close(REAL_FACE))

    def test_tight_chest_up_photo_is_allowed(self) -> None:
        path = self.folder / "chest-up.jpg"
        Image.new("RGB", (800, 1000)).save(path, "JPEG")
        with patch("mirror.ai.framing.detect_faces", return_value=[FaceBox(300, 220, 270, 430)]):
            self.assertFalse(too_close(path))

    def test_face_filling_half_the_frame_is_too_close(self) -> None:
        path = self.folder / "close-up.jpg"
        Image.new("RGB", (600, 900)).save(path, "JPEG")
        with patch("mirror.ai.framing.detect_faces", return_value=[FaceBox(120, 100, 360, 450)]):
            self.assertTrue(too_close(path))

    def test_standing_back_passes(self) -> None:
        path = self.folder / "back.jpg"
        path.write_bytes(standing_back_photo())
        self.assertFalse(too_close(path))

    def test_photo_without_a_face_is_left_to_the_service(self) -> None:
        path = self.folder / "plain.jpg"
        Image.new("RGB", (900, 1200), (76, 98, 125)).save(path, "JPEG")
        self.assertFalse(too_close(path))
