"""Regenerate every raster logo asset from the single source logo.png.

Run after replacing logo.png:  python scripts/gen_logo_assets.py
Needs Pillow (`pip install pillow`); nothing else in the app depends on it.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "logo.png"

PNG_TARGETS = {
    ROOT / "extension/icons/icon16.png": 16,
    ROOT / "extension/icons/icon32.png": 32,
    ROOT / "extension/icons/icon48.png": 48,
    ROOT / "extension/icons/icon128.png": 128,
    ROOT / "frontend/public/logo.png": 256,
}
ICO_TARGET = ROOT / "frontend/src/app/favicon.ico"
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]


def square(image: Image.Image) -> Image.Image:
    """Center-crop to a square first — keeps corner watermarks/artifacts on a
    wide source out of the trim bbox below."""
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    return image.crop((left, top, left + side, top + side))


def trimmed(image: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """Crop the dead border off the mark so it stays legible at 16px."""
    box = image.convert("L").point(lambda p: 255 if p > 24 else 0).getbbox()
    if box is None:
        return image
    pad = int(max(image.size) * pad_ratio)
    left, top, right, bottom = box
    return image.crop(
        (
            max(left - pad, 0),
            max(top - pad, 0),
            min(right + pad, image.width),
            min(bottom + pad, image.height),
        )
    )


def main() -> None:
    source = trimmed(square(Image.open(SOURCE).convert("RGBA")))

    for path, size in PNG_TARGETS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.LANCZOS).save(path)
        print(f"{path.relative_to(ROOT)}  {size}x{size}")

    source.save(ICO_TARGET, sizes=ICO_SIZES)
    print(f"{ICO_TARGET.relative_to(ROOT)}  {ICO_SIZES}")


if __name__ == "__main__":
    main()
