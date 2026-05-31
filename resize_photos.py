"""
resize_photos.py
================
Run from the repo root:  python resize_photos.py

Processes all JPEGs/PNGs in data/photos/:
1. Centre-crops to square (so all photos have the same aspect ratio)
2. Resizes to MAX_PX × MAX_PX (no upscaling)
3. Saves as JPEG at JPEG_QUALITY (converts PNG → JPEG)
4. Skips files already at the right size

A backup of each original is saved alongside as {name}_original.jpg
before the first crop/resize.
"""

from pathlib import Path
from PIL import Image

PHOTOS_DIR   = Path("data/photos")
MAX_PX       = 1200
JPEG_QUALITY = 85


def centre_crop_square(img: Image.Image) -> Image.Image:
    """Crop image to a square centred on the middle."""
    w, h   = img.size
    side   = min(w, h)
    left   = (w - side) // 2
    top    = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def resize_photos():
    patterns = ["*.jpg", "*.jpeg", "*.png"]
    photos   = []
    for p in patterns:
        photos.extend(PHOTOS_DIR.glob(p))
    # Exclude backup files
    photos = [p for p in photos if "_original" not in p.stem]

    if not photos:
        print(f"No photo files found in {PHOTOS_DIR}")
        return

    print(f"Found {len(photos)} photo(s) in {PHOTOS_DIR}\n")

    for path in sorted(photos):
        with Image.open(path) as img:
            original_size = path.stat().st_size
            w, h = img.size
            side = min(w, h)

            already_square = (w == h)
            already_small  = (max(w, h) <= MAX_PX)

            if already_square and already_small:
                print(f"  SKIP  {path.name}  ({w}×{h}, already square and small)")
                continue

            # Save backup of original before any modification
            backup_path = path.with_stem(path.stem + "_original").with_suffix(".jpg")
            if not backup_path.exists():
                if img.mode != "RGB":
                    img.convert("RGB").save(backup_path, "JPEG", quality=95)
                else:
                    img.save(backup_path, "JPEG", quality=95)
                print(f"  BACKUP saved: {backup_path.name}")

            # 1. Centre crop to square
            img_sq = centre_crop_square(img)

            # 2. Resize if still too large
            if img_sq.size[0] > MAX_PX:
                img_sq = img_sq.resize((MAX_PX, MAX_PX), Image.LANCZOS)

            # 3. Convert to RGB if needed (PNG with alpha etc.)
            if img_sq.mode != "RGB":
                img_sq = img_sq.convert("RGB")

            # 4. Save as JPEG (overwrites original, including PNGs)
            out_path = path.with_suffix(".jpg")
            img_sq.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True)

            # Remove original PNG if we just converted it
            if path.suffix.lower() == ".png" and out_path != path:
                path.unlink()
                print(f"  CONVERTED PNG→JPG: {path.name} → {out_path.name}")

            new_size = out_path.stat().st_size
            new_w, new_h = img_sq.size
            print(
                f"  OK    {path.name}  "
                f"{w}×{h} → {new_w}×{new_h}  "
                f"{original_size/1024:.0f}KB → {new_size/1024:.0f}KB"
            )

    print("\nDone.")


if __name__ == "__main__":
    resize_photos()
