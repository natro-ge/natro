"""Draws Natro's icon: an "N" in one stroke whose last stroke ends in the green dot of the dock, the
dot that swells with your voice. Writes branding/icon.svg and branding/icon.png (1024 px), which
`npx tauri icon` turns into the desktop app's icons; the Android and website copies are written
by hand from the same numbers (the 108 grid below is Android's adaptive-icon grid).

    .venv\\Scripts\\python.exe scripts\\make-icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "branding"
INK_TOP, INK_BOTTOM = (28, 32, 38), (12, 14, 16)
GREEN_DARK, GREEN_LIGHT = (16, 130, 12), (15, 185, 18)
# The N on a 108 grid, centered, and the dot at the end of its last stroke.
N = [(40, 72), (40, 40), (68, 72), (68, 45)]
DOT, DOT_RADIUS, STROKE = (68, 35), 6.5, 7.5
# For an icon that fills its square (not Android's, which shrinks the foreground to a safe zone).
ZOOM = 1.22


def render(size=1024):
    scale = 4  # drawn four times larger, then shrunk, for smooth edges
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # The tile: a rounded square, lighter at the top.
    gradient = Image.new("RGBA", (big, big))
    pixels = gradient.load()
    for y in range(big):
        mix = y / big
        row = tuple(round(INK_TOP[i] + (INK_BOTTOM[i] - INK_TOP[i]) * mix) for i in range(3)) + (255,)
        for x in range(big):
            pixels[x, y] = row
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.225), fill=255)
    image.paste(gradient, (0, 0), mask)

    def at(point):
        return ((point[0] - 54) * ZOOM + 54) * big / 108, ((point[1] - 54) * ZOOM + 54) * big / 108

    cx, cy = at(DOT)
    radius = DOT_RADIUS * ZOOM * big / 108
    # The dot's soft glow, under the N.
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for step in range(12, 0, -1):
        grow = radius * (1 + step * 0.16)
        glow_draw.ellipse((cx - grow, cy - grow, cx + grow, cy + grow), fill=(*GREEN_LIGHT, round(9 * (13 - step) / 3)))
    image.alpha_composite(glow)
    draw = ImageDraw.Draw(image)
    width = STROKE * ZOOM * big / 108
    for start, end in zip(N, N[1:]):
        draw.line((*at(start), *at(end)), fill=(255, 255, 255, 255), width=round(width))
    for point in N[:-1]:  # round joins; the last end is round too
        x, y = at(point)
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=(255, 255, 255, 255))
    x, y = at(N[-1])
    draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=(255, 255, 255, 255))

    draw = ImageDraw.Draw(image)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*GREEN_DARK, 255))
    inner = radius * 0.82
    draw.ellipse((cx - inner * 0.9, cy - inner * 1.0, cx + inner * 1.1, cy + inner * 0.9), fill=(*GREEN_LIGHT, 255))
    return image.resize((size, size), Image.LANCZOS)


def svg():
    def p(point):
        return f"{(point[0] - 54) * ZOOM + 54:.1f},{(point[1] - 54) * ZOOM + 54:.1f}"

    cx, cy = ((DOT[0] - 54) * ZOOM + 54, (DOT[1] - 54) * ZOOM + 54)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" role="img" aria-label="Natro">
  <defs>
    <linearGradient id="ink" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1c2026"/><stop offset="1" stop-color="#0c0e10"/></linearGradient>
    <linearGradient id="green" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#10820c"/><stop offset="1" stop-color="#0fb912"/></linearGradient>
    <radialGradient id="glow"><stop offset="0.45" stop-color="#0fb912" stop-opacity=".5"/><stop offset="1" stop-color="#0fb912" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="108" height="108" rx="24" fill="url(#ink)"/>
  <path d="M{" L".join(p(point) for point in N)}" fill="none" stroke="#fff" stroke-width="{STROKE * ZOOM:.1f}" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{DOT_RADIUS * ZOOM * 2.4:.1f}" fill="url(#glow)"/>
  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{DOT_RADIUS * ZOOM:.1f}" fill="url(#green)"/>
</svg>
"""


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8")
    render().save(OUT / "icon.png")
    print(f"wrote {OUT / 'icon.svg'} and {OUT / 'icon.png'}")
