"""Draws Natro's icon: the green dot of the dock, alone, with its soft glow and nothing behind it.
Writes shared/branding/icon.svg and shared/branding/icon.png (1024 px, see-through around the glow), which
`npx tauri icon` turns into the desktop app's icons; the Android and dashboard copies are written
by hand from the same numbers (the 108 grid below is Android's adaptive-icon grid).

    .venv\\Scripts\\python.exe scripts\\make-icon.py
"""
from math import hypot
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "shared" / "branding"
GREEN_DARK, GREEN_LIGHT = (16, 130, 12), (15, 185, 18)
# On a 108 grid, centered: the dot, and its glow, at full strength out to GLOW_SOLID of its radius, then fading out.
DOT_RADIUS, GLOW_RADIUS = 30, 52
GLOW_ALPHA, GLOW_SOLID = 0.55, 0.35


def render(size=1024):
    center = size / 2
    dot, glow = DOT_RADIUS * size / 108, GLOW_RADIUS * size / 108

    def glow_alpha(x, y):
        out = hypot(x + 0.5 - center, y + 0.5 - center) / glow
        fade = 1 if out <= GLOW_SOLID else max(0.0, 1 - (out - GLOW_SOLID) / (1 - GLOW_SOLID))
        return round(255 * GLOW_ALPHA * fade)

    def green(x, y):
        # Dark at the top left, light at the bottom right, across the dot: the dock's 135° gradient.
        mix = min(1.0, max(0.0, (x + y + 1 - 2 * (center - dot)) / (4 * dot)))
        return tuple(round(GREEN_DARK[i] + (GREEN_LIGHT[i] - GREEN_DARK[i]) * mix) for i in range(3))

    pixels = [(x, y) for y in range(size) for x in range(size)]
    image = Image.new("RGBA", (size, size))
    image.putdata([(*GREEN_LIGHT, glow_alpha(x, y)) for x, y in pixels])

    scale = 4  # the dot's edge drawn four times larger, then shrunk, to be smooth
    edge = Image.new("L", (size * scale, size * scale), 0)
    box = [(center + side * dot) * scale for side in (-1, -1, 1, 1)]
    ImageDraw.Draw(edge).ellipse(box, fill=255)
    fill = Image.new("RGB", (size, size))
    fill.putdata([green(x, y) for x, y in pixels])
    fill = fill.convert("RGBA")
    fill.putalpha(edge.resize((size, size), Image.LANCZOS))
    return Image.alpha_composite(image, fill)


def svg():
    def hex_of(color):
        return "#" + "".join(f"{c:02x}" for c in color)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" role="img" aria-label="Natro">
  <defs>
    <linearGradient id="green" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{hex_of(GREEN_DARK)}"/><stop offset="1" stop-color="{hex_of(GREEN_LIGHT)}"/></linearGradient>
    <radialGradient id="glow"><stop offset="{GLOW_SOLID}" stop-color="{hex_of(GREEN_LIGHT)}" stop-opacity="{GLOW_ALPHA}"/><stop offset="1" stop-color="{hex_of(GREEN_LIGHT)}" stop-opacity="0"/></radialGradient>
  </defs>
  <circle cx="54" cy="54" r="{GLOW_RADIUS}" fill="url(#glow)"/>
  <circle cx="54" cy="54" r="{DOT_RADIUS}" fill="url(#green)"/>
</svg>
"""


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8")
    render().save(OUT / "icon.png")
    print(f"wrote {OUT / 'icon.svg'} and {OUT / 'icon.png'}")
