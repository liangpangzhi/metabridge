#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate all PNG icons required by fnOS FPK from the DVD-drive artwork."""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "src" / "metabridge"


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 10
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size // 5,
                        fill=(37, 99, 235, 255))
    d.rounded_rectangle([pad + size // 14, pad + size // 14,
                         size - pad - size // 14, size - pad - size // 14],
                        radius=size // 7, outline=(147, 197, 253, 255), width=max(1, size // 40))
    # stylised bridge / media strips
    c = size // 2
    d.rounded_rectangle([c - size // 3, c - size // 9, c + size // 3, c - size // 22],
                        radius=size // 30, fill=(255, 255, 255, 240))
    d.rounded_rectangle([c - size // 3, c + size // 22, c + size // 3, c + size // 9],
                        radius=size // 30, fill=(191, 219, 254, 240))
    try:
        font = ImageFont.truetype("arial.ttf", size // 4)
    except Exception:
        font = ImageFont.load_default()
    d.text((c, c - size // 16), "M", font=font, fill=(37, 99, 235, 255), anchor="mm")
    return img


SOURCE = HERE.parent / "assets" / "app_icon_source.png"


def icon_for(size):
    if SOURCE.is_file():
        with Image.open(SOURCE) as im:
            return im.convert("RGBA").resize((size, size), Image.LANCZOS)
    return draw_icon(size)


def main():
    # fnOS expands "images/icon_{0}.png" to these concrete sizes on the desktop.
    # Underscores are required; hyphens produce a blank desktop icon.
    sizes = (16, 24, 32, 48, 64, 72, 96, 128, 256)
    ui_images = APP / "app" / "ui" / "images"
    if ui_images.is_dir():
        for old in ui_images.glob("icon-*.png"):
            old.unlink()
    targets = [(APP / "ICON.PNG", 64), (APP / "ICON_256.PNG", 256)]
    targets += [(ui_images / ("icon_%d.png" % s), s) for s in sizes]
    for path, size in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        icon_for(size).save(path)
        print("wrote", path, size)


if __name__ == "__main__":
    main()
