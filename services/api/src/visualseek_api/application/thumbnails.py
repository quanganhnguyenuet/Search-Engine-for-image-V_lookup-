"""Small WebP copies of corpus images for grids, cached on disk.

Originals average about 200 KB (a few are several MB) and travel up the demo machine's uplink,
while a grid cell only needs a few hundred pixels. The cache key is the asset id, a content
hash, so a cached file never goes stale.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import structlog

# Longest edge in pixels; the aspect ratio is kept and small images are never enlarged.
THUMB_EDGE = 480
QUALITY = 78
# An original this small is already cheap to send.
SMALL_BYTES = 30 * 1024


def thumbnail_path(cache_dir: Path, asset_id: str) -> Path:
    return cache_dir / str(THUMB_EDGE) / asset_id[:2] / f"{asset_id}.webp"


def thumbnail(source: Path, asset_id: str, cache_dir: Path) -> Path:
    """The thumbnail of ``source``; ``source`` itself when it is small or cannot be resized."""
    target = thumbnail_path(cache_dir, asset_id)
    if target.exists():
        return target
    try:
        if source.stat().st_size <= SMALL_BYTES:
            return source
        _write(source, target)
    except Exception as error:  # unreadable format, huge pixel count, full disk, no Pillow
        structlog.get_logger(__name__).warning(
            "thumbnail_failed", asset_id=asset_id, error=str(error)[:200]
        )
        # Another request may have written it meanwhile (Windows refuses to replace an open file).
        return target if target.exists() else source
    return target


def _write(source: Path, target: Path) -> None:
    from PIL import Image, ImageOps

    with Image.open(source) as image:
        image.draft("RGB", (THUMB_EDGE, THUMB_EDGE))  # JPEG: decode at a smaller scale
        # Browsers rotate the original by its EXIF tag; the copy must look the same.
        frame = ImageOps.exif_transpose(image)
        # A colour profile only fits the pixels it was made for, not a converted CMYK image.
        profile = frame.info.get("icc_profile") if frame.mode in ("RGB", "RGBA") else None
        transparent = frame.mode in ("RGBA", "LA", "PA") or "transparency" in frame.info
        mode = "RGBA" if transparent else "RGB"
        if frame.mode != mode:
            frame = frame.convert(mode)
        frame.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)

        target.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed, so a reader never sees half a file.
        handle, name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        os.close(handle)
        temporary = Path(name)
        try:
            frame.save(temporary, "WEBP", quality=QUALITY, icc_profile=profile)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
