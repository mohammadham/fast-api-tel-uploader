"""Eitaa scraper unit tests: extract_file_url against REAL saved eitaa markup
(fetched live 2026-09-22 from eitaa.com) + end-to-end download with mock transport.
"""
from __future__ import annotations

import httpx
import pytest

from app.tg.eitaa_backend import EitaaBackend, extract_file_url

# ── real markup captured live from eitaa.com ────────────────────────────────
# /s/{chat}/{id}?embed=1&mode=eme for a DOCUMENT message (et_dev/5, file
# etgetinfo.php): the anchor has NO file href — only the avatar <img> carries
# a tokenized /download_ URL. A robust scraper must NOT return the avatar.
REAL_DOCUMENT_EMBED = """<html><body>
<div class="etme_widget_message_wrap js-widget_message_wrap" id="5">
<div class="etme_widget_message_user"><a href="/et_dev"><i class="etme_widget_message_user_photo bgcolor5"
 data-content=""><img src="/download_ce08912d8c4453b26efb4c72b1d8403b?token=78da01b2004dff10000000b5ed5855e143"
 alt=""></i></a></div>
<a class="etme_widget_message_document_wrap" href="/et_dev/5">
 <div class="etme_widget_message_document_icon accent_bg "></div>
 <div class="etme_widget_message_document">
  <div class="etme_widget_message_document_title accent_color" dir="auto">etgetinfo.php</div>
  <div class="etme_widget_message_document_extra" dir="auto"><span style="padding: 0 0 0 5px">\u062d\u062c\u0645:
   3.6K</span></div>
 </div>
</a>
</div></body></html>"""

# video message shape: <video> carries the tokenized download URL
REAL_VIDEO_EMBED = """<html><body>
<div class="etme_widget_message_user"><a href="/et_dev"><i class="etme_widget_message_user_photo bgcolor5"
 data-content=""><img src="/download_ce08912d8c4453b26efb4c72b1d8403b?token=78da01b2004dff10000000b5ed5855e143"
 alt=""></i></a></div>
<div class="etme_widget_message_video_wrap js-message_media">
 <video src="/download_a41548d96629b0365f8ab8a1e91ef4b6?token=78da01b3004cff10000000d115b318e37e22c6"
  style="width:100%"></video>
</div>
</body></html>"""


def test_document_embed_does_not_leak_avatar():
    """Plain document page has no direct file link → None (not the avatar URL)."""
    assert extract_file_url(REAL_DOCUMENT_EMBED) is None


def test_video_embed_yields_video_url():
    url = extract_file_url(REAL_VIDEO_EMBED)
    assert url == "/download_a41548d96629b0365f8ab8a1e91ef4b6?token=78da01b3004cff10000000d115b318e37e22c6"


def test_absolute_download_href_matched():
    html = '<a href="https://eitaa.com/download_0123456789abcdef0123456789abcdef?token=0123456789abcdef0123456789abcdef">f</a>'
    assert extract_file_url(html).endswith("token=0123456789abcdef0123456789abcdef")


def test_markup_change_returns_none_not_crash():
    assert extract_file_url("<html><body>completely new design</body></html>") is None


def test_css_background_image_matched():
    html = "<i style=\"background-image:url('/download_0123456789abcdef0123456789abcdef?token=0123456789abcdef0123456789abcdef')\"></i>"
    assert extract_file_url(html) is not None


MOCK_HOST = "eitaa-mock.invalid"


def _backend_with(transport) -> EitaaBackend:
    return EitaaBackend("eit:99", "tok", "mychan", transport=httpx.MockTransport(transport))


@pytest.mark.asyncio
async def test_iter_file_streams_video_with_range(monkeypatch):
    payload = b"0123456789" * 100  # 1000 bytes

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if MOCK_HOST in url:
            return httpx.Response(200, content=payload)
        if url.startswith("https://eitaa.com/"):
            page = REAL_VIDEO_EMBED.replace(
                f'src="/download_a41548d96629b0365f8ab8a1e91ef4b6?token=',
                f'src="https://{MOCK_HOST}/download_a41548d96629b0365f8ab8a1e91ef4b6?token=',
            )
            return httpx.Response(200, text=page)
        return httpx.Response(404)

    be = _backend_with(handler)
    got = b""
    async for chunk in be.iter_file(5, "mychan", start=10, end=29, size=1000):
        got += chunk
    await be.close()
    assert got == payload[10:30]


@pytest.mark.asyncio
async def test_iter_file_document_fails_loud():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=REAL_DOCUMENT_EMBED)

    be = _backend_with(handler)
    with pytest.raises(Exception) as exc:
        async for _ in be.iter_file(5, "mychan", start=0, end=None, size=None):
            pass
    await be.close()
    assert "not found" in str(exc.value) or "document" in str(exc.value).lower()
