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
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def square(image: Image.Image) -> Image.Image:
    """Center-crop to a square first — keeps corner watermarks/artifacts on a
    wide source out of the trim bbox below."""
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    return image.crop((left, top, left + side, top + side))


def trimmed(image: Image.Image, pad_ratio: float = 0.04) -> Image.Image:
    """Crop the dead border off the mark so it stays legible at 16px.

    On a logo with transparency the alpha channel is what says where the mark
    is -- luminance alone calls a transparent white background "content" and
    trims nothing, and a transparent black one "content" too. Falls back to
    luminance for a flat RGB source.
    """
    if image.getchannel("A").getextrema()[0] < 255:
        mask = image.getchannel("A").point(lambda p: 255 if p > 24 else 0)
    else:
        mask = image.convert("L").point(lambda p: 255 if p > 24 else 0)

    box = mask.getbbox()
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


def padded_square(image: Image.Image) -> Image.Image:
    """Centre the trimmed mark on a square canvas.

    Every target here is square (favicons and extension icons are declared at
    NxN), so a non-square source must be padded rather than squashed -- PIL
    keeps the aspect ratio when writing an .ico, which is how a 256x246 mark
    ends up declared as a 256x256 icon and renders subtly squashed.
    """
    side = max(image.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def main() -> None:
    source = padded_square(trimmed(square(Image.open(SOURCE).convert("RGBA"))))

    for path, size in PNG_TARGETS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        source.resize((size, size), Image.LANCZOS).save(path)
        print(f"{path.relative_to(ROOT)}  {size}x{size}")

    source.save(ICO_TARGET, sizes=ICO_SIZES)
    print(f"{ICO_TARGET.relative_to(ROOT)}  {ICO_SIZES}")


if __name__ == "__main__":
    main()
