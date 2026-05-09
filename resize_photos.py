"""
resize_photos.py
================
Run from the repo root:  python resize_photos.py

Resizes all JPEGs in data/photos/ in place.
- Max 1200px on the longest side (no upscaling)
- JPEG quality 85%
- Skips anything already within limits
"""

import os
from pathlib import Path
from PIL import Image

PHOTOS_DIR  = Path("data/photos")
MAX_PX      = 1200
JPEG_QUALITY = 85

def resize_photos():
    photos = list(PHOTOS_DIR.glob("*.jpg")) + list(PHOTOS_DIR.glob("*.jpeg"))

    if not photos:
        print(f"No JPEG files found in {PHOTOS_DIR}")
        return

    print(f"Found {len(photos)} photo(s) in {PHOTOS_DIR}\n")

    for path in sorted(photos):
        with Image.open(path) as img:
            original_size = path.stat().st_size
            w, h = img.size

            if max(w, h) <= MAX_PX:
                print(f"  SKIP  {path.name}  ({w}×{h}, already small enough)")
                continue

            # Resize preserving aspect ratio
            img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
            new_w, new_h = img.size

            # Convert to RGB if needed (e.g. PNG with alpha converted to JPEG)
            if img.mode != "RGB":
                img = img.convert("RGB")

            img.save(path, "JPEG", quality=JPEG_QUALITY, optimize=True)
            new_size = path.stat().st_size

            print(
                f"  OK    {path.name}  "
                f"{w}×{h} → {new_w}×{new_h}  "
                f"{original_size/1024:.0f}KB → {new_size/1024:.0f}KB"
            )

    print("\nDone.")

if __name__ == "__main__":
    resize_photos()
