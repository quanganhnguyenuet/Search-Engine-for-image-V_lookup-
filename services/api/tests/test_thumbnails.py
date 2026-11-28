from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from visualseek_api.application.thumbnails import SMALL_BYTES, THUMB_EDGE, thumbnail
from visualseek_api.core.config import get_settings
from visualseek_api.main import app

Image = pytest.importorskip("PIL.Image")
ASSET = "ab" * 32


def noisy(path: Path, size: tuple[int, int], mode: str = "RGB", **save) -> Path:
    """Random pixels, so the file is well above the small-file limit."""
    import os

    image = Image.frombytes(mode, size, os.urandom(size[0] * size[1] * len(mode)))
    image.save(path, **save)
    return path


def test_large_image_is_shrunk_to_webp_and_cached(tmp_path) -> None:
    source = noisy(tmp_path / "big.png", (1600, 800))

    small = thumbnail(source, ASSET, tmp_path / "cache")

    with Image.open(small) as image:
        assert (image.format, image.size) == ("WEBP", (THUMB_EDGE, THUMB_EDGE // 2))
    source.unlink()
    assert thumbnail(source, ASSET, tmp_path / "cache") == small
    assert not list((tmp_path / "cache").rglob("*.tmp"))


def test_transparency_is_kept(tmp_path) -> None:
    source = noisy(tmp_path / "alpha.png", (600, 600), "RGBA")

    with Image.open(thumbnail(source, ASSET, tmp_path / "cache")) as image:
        assert image.mode == "RGBA"


def test_exif_rotation_is_applied(tmp_path) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # the camera was turned: show the picture rotated by 90 degrees
    source = noisy(tmp_path / "turned.jpg", (1200, 600), quality=95, exif=exif)

    with Image.open(thumbnail(source, ASSET, tmp_path / "cache")) as image:
        assert image.size == (THUMB_EDGE // 2, THUMB_EDGE)


def test_small_or_unreadable_files_are_served_as_they_are(tmp_path) -> None:
    small = tmp_path / "small.png"
    Image.new("RGB", (900, 900), "white").save(small)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"x" * (SMALL_BYTES + 1))

    assert thumbnail(small, ASSET, tmp_path / "cache") == small
    assert thumbnail(broken, "cd" * 32, tmp_path / "cache") == broken


def test_image_route_serves_the_thumbnail_only_when_asked(tmp_path, monkeypatch) -> None:
    source = noisy(tmp_path / "photo.png", (1000, 1000))
    monkeypatch.setenv("THUMBS_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()

    class Search:
        def image_file(self, asset_id: str):
            return source if asset_id == ASSET else None

    try:
        with TestClient(app) as client:
            app.state.search = Search()
            full = client.get(f"/api/v1/images/{ASSET}")
            small = client.get(f"/api/v1/images/{ASSET}?w={THUMB_EDGE}")
            other = client.get(f"/api/v1/images/{ASSET}?w=481")
            unknown = client.get(f"/api/v1/images/{'0' * 64}?w={THUMB_EDGE}")
    finally:
        app.state.search = None
        get_settings.cache_clear()

    assert full.content == source.read_bytes()
    assert small.headers["content-type"] == "image/webp"
    assert len(small.content) < len(full.content) / 2
    assert (other.status_code, unknown.status_code) == (400, 404)
