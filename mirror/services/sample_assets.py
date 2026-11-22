from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def _mix(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second, strict=True))


def create_sample_gown(
    path: Path,
    base: tuple[int, int, int],
    accent: tuple[int, int, int],
    distinguished: bool = False,
) -> None:
    """Create an original transparent sample garment illustration."""
    width, height = 1000, 1400
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse((170, 1280, 830, 1370), fill=(0, 0, 0, 75))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))

    draw = ImageDraw.Draw(image)
    left_sleeve = [(420, 300), (305, 340), (150, 580), (55, 960), (235, 1010), (360, 720), (400, 560)]
    right_sleeve = [(580, 300), (695, 340), (850, 580), (945, 960), (765, 1010), (640, 720), (600, 560)]
    robe = [(410, 285), (590, 285), (655, 520), (725, 1285), (500, 1340), (275, 1285), (345, 520)]
    draw.polygon(left_sleeve, fill=(*_mix(base, (0, 0, 0), 0.12), 255))
    draw.polygon(right_sleeve, fill=(*_mix(base, (0, 0, 0), 0.22), 255))
    draw.polygon(robe, fill=(*base, 255))

    for index in range(28):
        t = index / 27
        y = round(300 + t * 1020)
        half = round(92 + t * 205)
        color = _mix(
            base,
            (255, 255, 255) if index % 5 == 0 else (0, 0, 0),
            0.045 if index % 5 == 0 else 0.018 * t,
        )
        draw.line((500 - half, y, 500 + half, y), fill=(*color, 110), width=4)

    for offset, alpha, line_width in [(-150, 42, 7), (-82, 66, 8), (-24, 35, 5), (55, 55, 8), (132, 32, 6)]:
        points = []
        for y in range(390, 1310, 40):
            spread = (y - 390) / 920
            x = 500 + offset * (0.46 + spread * 0.62) + math.sin(y / 120 + offset) * 5
            points.append((x, y))
        draw.line(points, fill=(255, 255, 255, alpha), width=line_width)
        draw.line(
            [(x + 12, y) for x, y in points],
            fill=(0, 0, 0, alpha + 12),
            width=max(3, line_width - 2),
        )

    dark = _mix(base, (0, 0, 0), 0.55)
    draw.polygon([(410, 285), (476, 340), (440, 1200), (370, 1280), (350, 520)], fill=(*dark, 255))
    draw.polygon([(590, 285), (524, 340), (560, 1200), (630, 1280), (650, 520)], fill=(*dark, 255))
    draw.line([(410, 285), (476, 340), (440, 1200)], fill=(*accent, 255), width=17)
    draw.line([(590, 285), (524, 340), (560, 1200)], fill=(*accent, 255), width=17)
    draw.arc((378, 200, 622, 430), 18, 162, fill=(*accent, 255), width=34)
    draw.arc((400, 220, 600, 400), 20, 160, fill=(*_mix(accent, (255, 255, 255), 0.25), 255), width=8)

    for direction in (-1, 1):
        for bar in range(3 if distinguished else 2):
            y = 760 + bar * 45
            if direction < 0:
                draw.line((125, y + 40, 310, y), fill=(*accent, 245), width=19)
            else:
                draw.line((875, y + 40, 690, y), fill=(*accent, 245), width=19)

    draw.line(left_sleeve[:4], fill=(*_mix(base, (255, 255, 255), 0.18), 130), width=5)
    draw.line(right_sleeve[:4], fill=(*_mix(base, (255, 255, 255), 0.12), 110), width=5)
    draw.ellipse((468, 308, 532, 372), fill=(*accent, 220), outline=(255, 244, 210, 150), width=3)
    if distinguished:
        draw.polygon([(500, 344), (523, 375), (512, 430), (488, 430), (477, 375)], fill=(245, 220, 155, 230))

    highlight = Image.new("RGBA", image.size, (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(highlight)
    hdraw.ellipse((250, 160, 620, 1280), fill=(255, 255, 255, 20))
    highlight = highlight.filter(ImageFilter.GaussianBlur(70))
    highlight.putalpha(ImageChops.multiply(highlight.getchannel("A"), image.getchannel("A")))
    image = Image.alpha_composite(image, highlight)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
