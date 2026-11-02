"""Draws the Deadline Hub icon (calendar page with a check mark) at every size we need."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BLUE, WHITE, RED = (47, 91, 234, 255), (255, 255, 255, 255), (229, 72, 77, 255)


def draw(size: int) -> Image.Image:
    s = size * 4  # draw big, scale down for smooth edges
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad, r = s * 0.06, s * 0.2
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=r, fill=BLUE)
    # calendar page
    x0, y0, x1, y1 = s * 0.2, s * 0.24, s * 0.8, s * 0.8
    d.rounded_rectangle([x0, y0, x1, y1], radius=s * 0.07, fill=WHITE)
    d.rectangle([x0, y0 + s * 0.04, x1, y0 + s * 0.15], fill=RED)
    d.rounded_rectangle([x0, y0, x1, y0 + s * 0.15], radius=s * 0.07, fill=RED)
    for cx in (0.36, 0.64):  # binder rings
        d.rounded_rectangle([s * (cx - 0.03), s * 0.17, s * (cx + 0.03), s * 0.3], radius=s * 0.03, fill=WHITE)
    # check mark
    w = max(int(s * 0.075), 2)
    d.line([(s * 0.33, s * 0.56), (s * 0.46, s * 0.68), (s * 0.68, s * 0.43)], fill=BLUE, width=w, joint="curve")
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    big = draw(256)
    big.save(ROOT / "packaging" / "icons" / "icon.png")
    big.save(ROOT / "packaging" / "icons" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    draw(512).save(ROOT / "packaging" / "icons" / "icon-512.png")
    for n in (16, 32, 48, 128):
        draw(n).save(ROOT / "extension" / "icons" / f"icon{n}.png")
    print("icons written")
