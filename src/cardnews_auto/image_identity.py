from pathlib import Path

from PIL import Image, ImageOps


def image_signature(path: Path) -> bytes:
    """Compare decoded content, independent of URL, metadata, encoding and resolution."""
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB").resize((32, 32), Image.Resampling.LANCZOS).tobytes()


def duplicate_of(signature: bytes, used: dict[int, bytes]) -> int | None:
    for number, other in used.items():
        differences = [abs(a - b) for a, b in zip(signature, other, strict=True)]
        # Conservative threshold for resizes/JPEG recompression; not semantic similarity.
        if sum(differences) / len(differences) <= 3 and sum(d > 24 for d in differences) <= len(differences) * .02:
            return number
    return None
