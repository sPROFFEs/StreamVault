#!/usr/bin/env python3
"""Regenerate committed brand assets; development-only Pillow/CairoSVG required."""
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image


ASSETS = Path(__file__).resolve().parents[1] / "assets"


def main():
    source = (ASSETS / "brand/mark.svg").read_text()
    (ASSETS / "favicon.svg").write_text(source)
    rendered = cairosvg.svg2png(bytestring=source.encode(), output_width=1024, output_height=1024)
    icon = Image.open(BytesIO(rendered)).convert("RGBA")
    for name, size in {"icon.png": 512, "favicon-16x16.png": 16, "favicon-32x32.png": 32,
                       "apple-touch-icon.png": 180, "android-chrome-192x192.png": 192,
                       "android-chrome-512x512.png": 512}.items():
        icon.resize((size, size), Image.Resampling.LANCZOS).save(ASSETS / name)
    icon.save(ASSETS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    maskable = source.replace('rx="112"', 'rx="0"')
    cairosvg.svg2png(bytestring=maskable.encode(), write_to=str(ASSETS / "android-chrome-maskable-512x512.png"),
                    output_width=512, output_height=512)


if __name__ == "__main__":
    main()
