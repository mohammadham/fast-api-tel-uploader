"""Real thumbnail generation for stored media.

The queue used to store a SECOND copy of the whole file as the "thumbnail"
message, so every image/video/PDF cost double in the storage channel (a 500 MB
video occupied 1 GB). This module builds an actual thumbnail instead and never
re-uploads the source:

- images  : Pillow scales to <= ``max_px`` on the long side and re-encodes JPEG
- videos  : one frame is grabbed with ffmpeg and scaled to <= ``max_px``
- PDFs    : nothing (no rasterizer is a hard dependency, and an ``<img>``
            pointing at PDF bytes never rendered anyway) — so PDFs now cost
            *half* of what they used to instead of double

Small images keep their original bytes/mime: there is nothing to gain from a
lossy re-encode at that size, and it keeps pre-existing thumbnails valid.

Both helpers are blocking (image decode / ffmpeg subprocess) — call them from
a worker thread (``await asyncio.to_thread(build_thumbnail, ...)``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

log = logging.getLogger("tgdrive.thumbs")

#: long-side bound of the generated thumbnail, in pixels
THUMB_MAX_PX = 320
#: JPEG quality of the generated thumbnail
THUMB_QUALITY = 80
#: images at or below this size are stored as-is (no lossy re-encode)
SMALL_IMAGE_BYTES = 256 * 1024

IMAGE_MIMES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}

_ffmpeg_cache: Optional[str] = None


@dataclass
class Thumbnail:
    """A ready-to-send thumbnail document."""

    data: bytes
    mime: str
    ext: str
    width: int = 0
    height: int = 0

    @property
    def size(self) -> int:
        return len(self.data)


def ffmpeg_exe() -> Optional[str]:
    """Locate an ffmpeg binary: bundled wheel first (works on CI/Linux), then
    the system PATH. ``None`` means video thumbnails are unavailable."""
    global _ffmpeg_cache
    if _ffmpeg_cache is not None:
        return _ffmpeg_cache or None
    exe = ""
    try:  # imageio-ffmpeg ships a static binary — no system install needed
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe() or ""
    except Exception:
        exe = shutil.which("ffmpeg") or ""
    if exe and not os.path.exists(exe):
        exe = shutil.which("ffmpeg") or ""
    _ffmpeg_cache = exe
    if not exe:
        log.warning("no ffmpeg binary: video thumbnails disabled")
    return exe or None


def wants_thumb(mime: str, size: int = 0) -> bool:
    """True when a thumbnail can be produced for this media type."""
    mime = (mime or "").lower()
    return mime in IMAGE_MIMES or mime.startswith("video/")


def _resample():
    from PIL import Image

    # Image.LANCZOS was removed in Pillow 10; Resampling.LANCZOS is the new name
    resampling = getattr(Image, "Resampling", Image)
    return getattr(resampling, "LANCZOS", 1)


def _image_thumbnail(path: str, mime: str, max_px: int, quality: int) -> Optional[Thumbnail]:
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im) or im
            try:  # animated gif/webp → first frame
                im.seek(0)
            except (EOFError, AttributeError):
                pass
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((max_px, max_px), _resample())
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=quality, optimize=True)
            width, height = im.size
    except Exception as exc:
        log.warning("image thumbnail failed: %s", str(exc)[:160])
        return None
    return Thumbnail(data=buf.getvalue(), mime="image/jpeg", ext=".jpg", width=width, height=height)


def _video_thumbnail(path: str, max_px: int, quality: int) -> Optional[Thumbnail]:
    exe = ffmpeg_exe()
    if not exe:
        return None
    # -ss before -i is the fast seek; scale=…:force_original_aspect_ratio=decrease
    # keeps the aspect and never upscales. -2 forces an even height (jpeg needs it).
    vf = f"scale={max_px}:-2:force_original_aspect_ratio=decrease"
    base = [exe, "-nostdin", "-loglevel", "error"]
    for seek in ("1", "0"):  # 1s avoids black intro frames; 0 is the fallback
        cmd = base + ["-ss", seek, "-i", path, "-frames:v", "1", "-vf", vf,
                      "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except Exception as exc:
            log.warning("video thumbnail failed: %s", str(exc)[:160])
            return None
        if proc.returncode == 0 and proc.stdout:
            data = proc.stdout
            width = height = 0
            try:
                from PIL import Image

                with Image.open(BytesIO(data)) as im:
                    width, height = im.size
            except Exception:
                pass  # size is informational only
            return Thumbnail(data=data, mime="image/jpeg", ext=".jpg", width=width, height=height)
    log.warning("video thumbnail: no frame decoded from %s", os.path.basename(path)[:60])
    return None


def build_thumbnail(
    path: str,
    mime: str,
    size: int = 0,
    *,
    max_px: int = THUMB_MAX_PX,
    quality: int = THUMB_QUALITY,
) -> Optional[Thumbnail]:
    """Build a thumbnail document for ``path``. ``None`` when none should be
    stored (unsupported type, undecodable media, missing ffmpeg) — the caller
    then stores nothing, which is always better than duplicating the file."""
    mime = (mime or "").lower()
    if not wants_thumb(mime):
        return None
    try:
        actual = os.path.getsize(path)
    except OSError:
        return None

    if mime in IMAGE_MIMES:
        if actual <= SMALL_IMAGE_BYTES:
            # nothing to gain from re-encoding; keep the exact original bytes
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                return None
            return Thumbnail(data=data, mime=mime, ext=IMAGE_MIMES[mime])
        thumb = _image_thumbnail(path, mime, max_px, quality)
        if thumb is None:
            return None  # undecodable/corrupt → store NOTHING (never duplicate the file)
        if thumb.size < actual:
            return thumb
        # re-encoding did not pay off (already tiny/optimised) → keep original
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return None
        return Thumbnail(data=data, mime=mime, ext=IMAGE_MIMES[mime])

    if mime.startswith("video/"):
        return _video_thumbnail(path, max_px, quality)

    return None