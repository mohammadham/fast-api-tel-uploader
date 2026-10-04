"""Thumbnails must be REAL thumbnails: the whole source file is never re-uploaded.

Covers app/services/thumbs.py (unit) and the upload→thumb→serve path (api).

The fixtures below deliberately generate *realistic* media: a flat-colour PNG
compresses to ~18 KB, which would fall under the "keep the original bytes"
threshold and hide the whole point of the feature, and a 2-second synthetic
video compresses to ~16 KB. So the image is per-row noise (~3 MB) and the video
carries noise too (~9 MB) — sizes a real photo/video would have.
"""
from __future__ import annotations

import asyncio
import io
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from io import BytesIO

import pytest

from app.services.thumbs import SMALL_IMAGE_BYTES, build_thumbnail, ffmpeg_exe, wants_thumb


# ── fixtures / helpers ────────────────────────────────────────────────
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def make_png(width: int = 1200, height: int = 900, *, noise: bool = True) -> bytes:
    """A real PNG. ``noise=True`` gives incompressible pixels (photo-sized)."""
    row = lambda: b"\x00" + (os.urandom(width * 3) if noise else b"\x20\x60\xc0" * width)
    raw = b"".join(row() for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 1))
            + _chunk(b"IEND", b""))


def make_mp4(path: str) -> bool:
    """Render a short, realistically sized MP4 (noise defeats compression)."""
    exe = ffmpeg_exe()
    if not exe:
        return False
    proc = subprocess.run(
        [exe, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
         "-vf", "noise=alls=6:allf=t", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-crf", "22", "-preset", "ultrafast", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
    )
    return proc.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0


# ── unit: build_thumbnail ─────────────────────────────────────────────
def test_large_image_is_scaled_and_reencoded(tmp_path):
    src = make_png(1200, 900)
    assert len(src) > SMALL_IMAGE_BYTES, "fixture must be photo-sized, not a flat colour"
    p = tmp_path / "big.png"
    p.write_bytes(src)

    thumb = build_thumbnail(str(p), "image/png", len(src))
    assert thumb is not None
    assert thumb.mime == "image/jpeg"
    assert thumb.ext == ".jpg"
    assert thumb.width <= 320 and thumb.height <= 320, f"not scaled: {thumb.width}x{thumb.height}"
    assert thumb.size < len(src) / 4, f"no storage win: {thumb.size} vs {len(src)}"
    assert thumb.data[:3] == b"\xff\xd8\xff", "must be a real JPEG stream"


def test_small_image_keeps_original_bytes_and_mime(tmp_path):
    """Below the threshold re-encoding would only lose quality, so keep the file."""
    src = make_png(4, 4, noise=False)
    assert len(src) <= SMALL_IMAGE_BYTES
    p = tmp_path / "tiny.png"
    p.write_bytes(src)

    thumb = build_thumbnail(str(p), "image/png", len(src))
    assert thumb is not None
    assert thumb.data == src
    assert thumb.mime == "image/png"
    assert thumb.ext == ".png"


def test_video_gets_a_single_small_frame(tmp_path):
    p = tmp_path / "clip.mp4"
    if not make_mp4(str(p)):
        pytest.skip("no ffmpeg binary available")
    size = p.stat().st_size

    thumb = build_thumbnail(str(p), "video/mp4", size)
    assert thumb is not None, "a decodable video must produce a thumbnail"
    assert thumb.mime == "image/jpeg"
    assert max(thumb.width, thumb.height) <= 320
    assert thumb.data[:3] == b"\xff\xd8\xff"
    assert thumb.size < size / 4, f"frame must be far smaller than the video: {thumb.size} vs {size}"


def test_pdf_and_unknown_types_get_no_thumbnail(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.7\nfake\n")
    # the whole point: never duplicate a file we cannot shrink
    assert build_thumbnail(str(p), "application/pdf", p.stat().st_size) is None
    assert build_thumbnail(str(p), "application/zip", p.stat().st_size) is None
    assert wants_thumb("application/pdf") is False
    assert wants_thumb("video/quicktime") is True


def test_undecodable_image_returns_none(tmp_path):
    """Large but broken → the builder gives up instead of storing junk."""
    p = tmp_path / "broken.png"
    p.write_bytes(b"not really a png" * 40_000)
    assert p.stat().st_size > SMALL_IMAGE_BYTES
    assert build_thumbnail(str(p), "image/png", p.stat().st_size) is None


def test_missing_file_returns_none(tmp_path):
    assert build_thumbnail(str(tmp_path / "nope.png"), "image/png", 10) is None


def test_max_px_is_honoured(tmp_path):
    """An explicit max_px must win over the module default, not be ignored."""
    src = make_png(1200, 900)
    p = tmp_path / "big.png"
    p.write_bytes(src)

    small = build_thumbnail(str(p), "image/png", len(src), max_px=96, quality=80)
    big = build_thumbnail(str(p), "image/png", len(src), max_px=640, quality=80)
    assert small is not None and big is not None
    assert max(small.width, small.height) <= 96, f"{small.width}x{small.height}"
    assert max(big.width, big.height) <= 640, f"{big.width}x{big.height}"
    assert small.size < big.size, "a smaller box must produce fewer bytes"
    # keep the aspect ratio (1200x900 → 4:3)
    assert abs((small.width / max(small.height, 1)) - 4 / 3) < 0.2


def test_quality_is_honoured(tmp_path):
    """Lower JPEG quality must yield fewer bytes at the same size."""
    src = make_png(1200, 900)
    p = tmp_path / "big.png"
    p.write_bytes(src)

    low = build_thumbnail(str(p), "image/png", len(src), max_px=320, quality=40)
    high = build_thumbnail(str(p), "image/png", len(src), max_px=320, quality=95)
    assert low is not None and high is not None
    assert (low.width, low.height) == (high.width, high.height)
    assert low.size < high.size, f"quality ignored: q40={low.size} q95={high.size}"


# ── api: upload → thumbnail → serve ───────────────────────────────────
async def _upload_and_wait(client, api_key: str, data: bytes, name: str, mime: str) -> dict:
    r = await client.post("/api/v1/files/upload",
                          files={"file": (name, io.BytesIO(data), mime)},
                          headers={"Authorization": f"Bearer {api_key}"})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    H = {"Authorization": f"Bearer {api_key}"}
    info: dict = {}
    for _ in range(100):
        info = (await client.get(f"/api/v1/files/{fid}", headers=H)).json()
        if info.get("status") in ("ready", "failed"):
            break
        await asyncio.sleep(0.1)
    assert info.get("status") == "ready", info
    return info


async def test_uploaded_image_thumbnail_is_not_a_second_copy(client, api_key):
    """The whole point of the feature: storage must NOT double for media."""
    from PIL import Image

    src = make_png(1400, 1000)
    H = {"Authorization": f"Bearer {api_key}"}
    info = await _upload_and_wait(client, api_key, src, "huge.png", "image/png")
    fid = info["id"]

    assert info["thumb_message_id"], "image upload must create a thumbnail message"
    assert info["thumb_mime"] == "image/jpeg"

    slug = (await client.post(f"/api/v1/files/{fid}/share", headers=H, json={"slug": "thumb-big"})).json()["slug"]

    r = await client.get(f"/{slug}/thumb")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) < len(src) / 4, "thumbnail must not be a copy of the source"
    assert r.content != src
    with Image.open(BytesIO(r.content)) as im:      # really decodable
        assert max(im.size) <= 320


async def test_uploaded_video_thumbnail_is_one_frame(client, api_key):
    tmpdir = tempfile.mkdtemp(prefix="tgdrive-thumb-")
    tmp = os.path.join(tmpdir, "clip.mp4")
    if not make_mp4(tmp):
        shutil.rmtree(tmpdir, ignore_errors=True)
        pytest.skip("no ffmpeg binary available")
    try:
        with open(tmp, "rb") as fh:
            data = fh.read()
        assert len(data) > 512 * 1024, "fixture video must be big enough for the ratio to mean something"
        info = await _upload_and_wait(client, api_key, data, "clip.mp4", "video/mp4")
        assert info["thumb_message_id"], "video upload must create a thumbnail message"
        assert info["thumb_mime"] == "image/jpeg"
        H = {"Authorization": f"Bearer {api_key}"}
        slug = (await client.post(f"/api/v1/files/{info['id']}/share", headers=H,
                                  json={"slug": "thumb-clip"})).json()["slug"]
        r = await client.get(f"/{slug}/thumb")
        assert r.status_code == 200, r.text
        assert len(r.content) < len(data) / 4
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)