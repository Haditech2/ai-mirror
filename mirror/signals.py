from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from mirror.models import EventSettings, Gown
from mirror.security import encrypt_secret
from mirror.services.sample_assets import create_sample_gown
from mirror.storage import storage

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
        "Azure & Gold Academic Gown",
        "Azure Blue & Gold",
        "An azure blue satin academic gown with broad pale gold front panels and fine gold piping, "
        "paired with a rounded blue tam and a long gold tassel.",
        "azure-gold-academic-gown.png",
        "azure-academic-tam.png",
    ),
    (
        "Green & Gold Graduation Gown",
        "Green & Gold",
        "An emerald green graduation robe with broad gold front panels and a single crest, "
        "paired with a green mortarboard with a gold band and tassel.",
        "green-gold-graduation-gown.png",
        "green-gold-mortarboard.png",
    ),
    (
        "Black & Crimson Doctoral Gown",
        "Black & Crimson",
        "A black academic gown with a crimson satin V-yoke, crimson front trim, a single chest crest, "
        "and a matching crimson velvet tam with a gold tassel.",
        "black-crimson-doctoral-gown.png",
        "crimson-doctoral-tam.png",
    ),
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

    for order, (name, color, description, base, accent, distinguished) in enumerate(SAMPLE_GOWNS, 1):
        slug = _slug(name)
        filename = f"{slug}.png"
        relative = f"gowns/{filename}"
        if not storage.exists(relative):
            with TemporaryDirectory(prefix="academic-mirror-gown-") as folder:
                path = Path(folder) / filename
                create_sample_gown(path, base, accent, distinguished)
                storage.write(relative, path.read_bytes())
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
            if filename and not storage.exists(f"gowns/{filename}"):
                storage.write(f"gowns/{filename}", (ASSET_ROOT / filename).read_bytes())
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
