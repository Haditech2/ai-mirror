from __future__ import annotations

import re
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from mirror.models import EventSettings, Gown
from mirror.security import encrypt_secret
from mirror.services.sample_assets import create_sample_gown

SAMPLE_GOWNS = [
    (
        "Traditional Black Academic Gown",
        "Black",
        "A timeless black academic robe with refined champagne facings and classic ceremonial drape.",
        (20, 23, 29),
        (210, 178, 102),
        False,
    ),
    (
        "Royal Blue Academic Gown",
        "Royal Blue",
        "A confident royal blue gown with luminous gold trim, designed for a distinguished scholarly portrait.",
        (29, 68, 145),
        (222, 188, 101),
        False,
    ),
    (
        "Burgundy Academic Gown",
        "Burgundy",
        "A rich burgundy ceremonial robe with warm gold accents and elegant, structured sleeves.",
        (112, 25, 49),
        (226, 184, 105),
        False,
    ),
    (
        "Emerald Green Academic Gown",
        "Emerald Green",
        "A deep emerald gown with champagne piping, evoking achievement, renewal and academic prestige.",
        (18, 101, 74),
        (226, 196, 119),
        False,
    ),
    (
        "Gold Ceremonial Gown",
        "Antique Gold",
        "A radiant antique-gold robe with midnight velvet facings for a bold, celebratory ceremonial look.",
        (184, 137, 57),
        (20, 31, 51),
        False,
    ),
    (
        "Professor's Distinguished Academic Gown",
        "Midnight & Gold",
        "The collection’s signature gown, featuring velvet panels, doctoral sleeve bars and a distinguished gold hood.",
        (12, 25, 48),
        (229, 194, 111),
        True,
    ),
]

ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "gowns"

# Real product photographs shipped with the app: (name, colour, description, gown file, cap file).
BUNDLED_GOWNS = [
    (
        "Royal Blue Graduation Gown & Cap",
        "Royal Blue",
        "A royal blue graduation gown with silver-white embroidered front panels, a scarlet satin "
        "V-hood and a matching mortarboard with a red tassel.",
        "royal-blue-graduation-gown.png",
        "royal-blue-graduation-cap.png",
    ),
    (
        "Gold Doctoral Gown & Tam",
        "Gold & Emerald",
        "A gold velvet doctoral gown with emerald velvet front facings, three emerald sleeve bars "
        "with white piping, embroidered crests and a matching gold velvet tam with tassel.",
        "gold-doctoral-gown.png",
        "gold-doctoral-cap.png",
    ),
]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


@receiver(post_migrate)
def seed_initial_data(sender, **kwargs) -> None:
    if sender.name != "mirror":
        return
    event, created = EventSettings.objects.get_or_create(pk=1)
    if created:
        event.ai_provider = settings.AI_PROVIDER
        event.replicate_model_owner = settings.REPLICATE_MODEL_OWNER
        event.replicate_model_name = settings.REPLICATE_MODEL_NAME
        event.replicate_model_version = settings.REPLICATE_MODEL_VERSION
        event.fashn_model_name = settings.FASHN_MODEL_NAME
        if settings.AI_API_KEY:
            event.ai_api_key_encrypted = encrypt_secret(settings.AI_API_KEY)
        event.save()

    user_model = get_user_model()
    if not user_model.objects.filter(username=settings.ADMIN_USERNAME).exists():
        user_model.objects.create_user(
            username=settings.ADMIN_USERNAME,
            password=settings.ADMIN_PASSWORD,
            is_staff=True,
        )

    gown_root = Path(settings.PRIVATE_STORAGE_ROOT) / "gowns"
    gown_root.mkdir(parents=True, exist_ok=True)
    for order, (name, color, description, base, accent, distinguished) in enumerate(SAMPLE_GOWNS, 1):
        slug = _slug(name)
        filename = f"{slug}.png"
        path = gown_root / filename
        if not path.exists():
            create_sample_gown(path, base, accent, distinguished)
        Gown.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "color": color,
                "description": description,
                "image_path": f"gowns/{filename}",
                "image_mime": "image/png",
                "is_enabled": True,
                "is_sample": True,
                "display_order": order,
            },
        )

    for name, color, description, gown_file, cap_file in BUNDLED_GOWNS:
        slug = _slug(name)
        for filename in (gown_file, cap_file):
            if filename and not (gown_root / filename).exists():
                shutil.copyfile(ASSET_ROOT / filename, gown_root / filename)
        Gown.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "color": color,
                "description": description,
                "image_path": f"gowns/{gown_file}",
                "image_mime": "image/png",
                "cap_image_path": f"gowns/{cap_file}" if cap_file else "",
                "is_enabled": True,
                "is_sample": False,
                "display_order": 0,
            },
        )
