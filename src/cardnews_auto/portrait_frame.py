from pathlib import Path
import hashlib
import os
import tempfile

from PIL import Image, ImageOps


def validate_publication_size(width: int, height: int) -> None:
    if width * 4 != height * 3:
        raise RuntimeError(
            f"배경 이미지가 {width}×{height}로 3:4 비율이 아닙니다. "
            "확대·크롭하지 않았습니다. 3:4 배경을 생성하거나 업로드하세요."
        )
    if width < 1080 or height < 1440:
        raise RuntimeError("배경 해상도가 1080×1440보다 작습니다. 화질 보호를 위해 확대하지 않았습니다.")


def publication_frame(source_path: Path, cache_dir: Path) -> Path:
    """Create the one publication background; never crop, pad, or upscale."""
    with Image.open(source_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        validate_publication_size(width, height)
        if image.size == (1080, 1440) and source_path.suffix.lower() == ".png":
            return source_path
        key = hashlib.sha256(source_path.read_bytes() + b"publication-1080x1440-v1").hexdigest()[:20]
        target = cache_dir / f"{key}.png"
        if not target.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            if image.size != (1080, 1440):
                image = image.resize((1080, 1440), Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".png", delete=False) as handle:
                temporary = Path(handle.name)
            try:
                image.save(temporary, "PNG")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            os.utime(target)  # keep a reused frame out of the cache prune
        return target
