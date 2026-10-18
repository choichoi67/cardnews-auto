import os
import time

from PIL import Image

from cardnews_auto import render
from cardnews_auto.cli import clean


def test_storage_downscales_prunes_and_links(tmp_path):
    big = tmp_path / "big.png"
    Image.new("RGB", (3000, 4000), "red").save(big)
    cached = render._download_cover_image(str(big), tmp_path / "cache")
    with Image.open(cached) as image:
        assert image.size == (2304, 3072)  # still exactly 3:4 for publication checks

    old = tmp_path / "cache" / "old.png"
    old.write_bytes(b"x" * 10)
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    assert render.prune_cache(tmp_path / "cache") == 10
    assert cached.exists() and not old.exists()

    for name in ("a", "b"):
        assets = tmp_path / "output" / name / "assets"
        assets.mkdir(parents=True)
        (assets / "template_01.psd").write_bytes(b"psd" * 1000)
    assert clean(tmp_path, 14) == 3000
    a, b = (tmp_path / "output" / name / "assets" / "template_01.psd" for name in ("a", "b"))
    assert os.path.samefile(a, b) and b.read_bytes() == b"psd" * 1000
