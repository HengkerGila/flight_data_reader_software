"""Generate packaging/icon.ico, the application icon, with Pillow.

Motif: a dark rounded square holding four subframe columns, the first one
highlighted like a received subframe in the live Frame View, under an amber
sync bar.  Sizes 16 to 256 px are embedded so Explorer, the taskbar, the
Start Menu and Apps & Features all get a crisp version.

    .venv/bin/python packaging/make_icon.py          # rewrites packaging/icon.ico
    .venv/bin/python packaging/make_icon.py out.png  # also writes a 256 px preview
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BACKGROUND = (27, 34, 48, 255)      # Midnight Blue window colour
COLUMN = (214, 221, 232, 96)        # pending columns: light, translucent
RECEIVED = (61, 123, 217, 255)      # the received column: highlight blue
SYNC = (217, 154, 30, 255)          # amber sync bar
EDGE = (12, 16, 24, 255)


def draw_master(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size * 0.03
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size * 0.19,
        fill=BACKGROUND,
        outline=EDGE,
        width=max(1, int(size * 0.012)),
    )
    left, right = size * 0.17, size * 0.83
    top, bottom = size * 0.30, size * 0.80
    columns = 4
    gap = size * 0.04
    width = (right - left - gap * (columns - 1)) / columns
    for index in range(columns):
        x0 = left + index * (width + gap)
        draw.rounded_rectangle(
            (x0, top, x0 + width, bottom),
            radius=size * 0.03,
            fill=RECEIVED if index == 0 else COLUMN,
        )
    draw.rounded_rectangle(
        (left, size * 0.18, right, size * 0.25), radius=size * 0.02, fill=SYNC
    )
    return image


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent
    master = draw_master(256)
    target = root / "icon.ico"
    master.save(target, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {target} ({target.stat().st_size} bytes, sizes {SIZES})")
    if len(argv) > 1:
        master.save(argv[1], format="PNG")
        print(f"wrote preview {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
