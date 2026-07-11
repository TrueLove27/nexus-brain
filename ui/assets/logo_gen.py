"""Generate Nexus logo and window icon assets."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent
LOGO_PATH = ASSETS / "nexus_logo.png"
ICON_PATH = ASSETS / "nexus_icon.ico"
LOGO_SM_PATH = ASSETS / "nexus_logo_sm.png"

BG = (6, 6, 11, 0)
CYAN = (217, 119, 87)
CYAN_SOFT = (232, 149, 122)
VIOLET = (196, 168, 130)
WHITE = (238, 242, 255)


def _hex_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    import math
    return [
        (cx + r * math.cos(math.radians(60 * i - 30)), cy + r * math.sin(math.radians(60 * i - 30)))
        for i in range(6)
    ]


def _draw_logo(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size * 0.38

    # Outer hex ring
    hex_pts = _hex_points(cx, cy, r)
    draw.polygon(hex_pts, outline=CYAN, width=max(2, size // 32))

    # Inner hex fill (subtle)
    inner = _hex_points(cx, cy, r * 0.72)
    draw.polygon(inner, fill=(0, 212, 255, 28))

    # Neural nodes
    nodes = [
        (cx, cy - r * 0.35, 5),
        (cx - r * 0.32, cy + r * 0.18, 4),
        (cx + r * 0.32, cy + r * 0.18, 4),
        (cx, cy + r * 0.05, 6),
    ]
    lines = [(0, 3), (1, 3), (2, 3), (0, 1), (0, 2), (1, 2)]
    for a, b in lines:
        ax, ay, _ = nodes[a]
        bx, by, _ = nodes[b]
        draw.line([(ax, ay), (bx, by)], fill=(124, 58, 237, 180), width=max(1, size // 64))

    for x, y, rad in nodes:
        draw.ellipse(
            (x - rad, y - rad, x + rad, y + rad),
            fill=CYAN if rad >= 6 else CYAN_SOFT,
        )

    # Center pulse
    pr = max(3, size // 18)
    draw.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=WHITE)

    glow = img.copy()
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(2, size // 24)))
    img = Image.alpha_composite(glow, img)
    return img


def ensure_assets() -> dict[str, Path]:
    ASSETS.mkdir(parents=True, exist_ok=True)
    if not LOGO_PATH.exists():
        _draw_logo(256).save(LOGO_PATH)
    if not LOGO_SM_PATH.exists():
        _draw_logo(48).save(LOGO_SM_PATH)
    if not ICON_PATH.exists():
        base = _draw_logo(256)
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        base.save(ICON_PATH, format="ICO", sizes=[(s, s) for s, _ in sizes])
    return {"logo": LOGO_PATH, "logo_sm": LOGO_SM_PATH, "icon": ICON_PATH}


if __name__ == "__main__":
    paths = ensure_assets()
    for k, p in paths.items():
        print(f"{k}: {p}")
