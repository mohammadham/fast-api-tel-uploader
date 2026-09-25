"""Files: direct + resumable upload, listing, delete, presigned links, public download."""
from __future__ import annotations

import mimetypes
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.models import FileRepo, FolderRepo, UploadSessionRepo, new_id, now
from ..core.rate_limit import quota
from ..core.settings_service import get_runtime
from ..core.state import get_db, state
from ..services.presign import make_link
from ..services.streaming import file_response
from .deps import get_admin_or_key, get_api_key, get_client_ip, get_current_admin, require_scope

router = APIRouter(prefix="/api/v1/files", tags=["files"])
public = APIRouter(tags=["public"])


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "file").replace("\\", "_").replace("/", "_").strip()
    return name[:180] or "file"


def _ext_blocked(name: str, blocked: str = "") -> bool:
    if not blocked:
        blocked = get_settings().blocked_extensions
    exts = {e.strip().lower() for e in blocked.split(",") if e.strip()}
    low = name.lower()
    return any(low.endswith(ext) for ext in exts)


async def _resolve_folder(db, x_folder: Optional[str], q_folder: Optional[str]):
    """Resolve the upload folder from X-Folder header (path) or ?folder= query.

    Nested paths ('projects/2026/reports') are created on demand. Returns
    (folder_id or None, canonical_path for the telegram caption tag).
    """
    raw = (x_folder or q_folder or "").strip()
    if not raw or raw in ("/", "."):
        return None, ""
    repo = FolderRepo(db)
    try:
        fid = await repo.resolve_path(raw, create=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return fid, await repo.path_of(fid)


@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    db=Depends(get_db),
    key=Depends(get_api_key),
):
    """Direct multipart upload (≤ max_upload_size runtime setting)."""
    require_scope(key, "write")
    s = get_settings()
    max_size = int(await get_runtime(db, "max_upload_size") or s.max_upload_size)
    blocked = str(await get_runtime(db, "blocked_extensions") or "")
    name = _safe_name(file.filename or "file")
    if _ext_blocked(name, blocked):
        raise HTTPException(status_code=415, detail="file type not allowed")
    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"

    os.makedirs(s.final_tmp_dir(), exist_ok=True)
    tmp_id = new_id("tmp")
    tmp_path = os.path.join(s.final_tmp_dir(), f"{tmp_id}.bin")
    file_id = new_id("f")
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(status_code=413, detail="file too large")
                out.write(chunk)
    except HTTPException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    backend = (key.get("backend") or "") or (await get_runtime(db, "default_backend")) or get_settings().default_backend
    storage_chat = (key.get("storage_chat") or "").strip()
    folder_id, folder_path = await _resolve_folder(db, request.headers.get("x-folder"), request.query_params.get("folder"))
    await FileRepo(db).create(file_id, name, size, mime, uploader=f"key:{key['id']}", source="api", backend=backend, folder_id=folder_id)
    await db.audit(f"key:{key['id']}", "file.upload", target=file_id, details=f"{size}B {name} backend={backend} storage_chat={storage_chat or 'default'} folder={folder_path or 'root'}")
    await state.queue.enqueue(
        "upload",
        {"file_id": file_id, "tmp_path": tmp_path, "size": size, "backend": backend, "storage_chat": storage_chat, "folder_path": folder_path, "webhook": request.query_params.get("webhook")},
        40,
    )
    quota.add(f"key:{key['id']}", size)
    return {"file_id": file_id, "name": name, "size": size, "status": "queued"}


class SessionIn(BaseModel):
    name: str
    size: int
    mime: str = ""


@router.post("/upload/session")
async def create_session(request: Request, body: SessionIn, key=Depends(get_api_key), db=Depends(get_db)):
    """Resumable chunked upload session."""
    require_scope(key, "write")
    s = get_settings()
    max_size = int(await get_runtime(db, "max_upload_size") or s.max_upload_size)
    if body.size > max_size:
        raise HTTPException(status_code=413, detail="file too large")
    name = _safe_name(body.name)
    if _ext_blocked(name, str(await get_runtime(db, "blocked_extensions") or "")):
        raise HTTPException(status_code=415, detail="file type not allowed")
    folder_id, folder_path = await _resolve_folder(db, request.headers.get("x-folder"), request.query_params.get("folder"))
    session_id = new_id("us")
    await UploadSessionRepo(db).create(session_id, name, body.size, body.mime or mimetypes.guess_type(name)[0] or "application/octet-stream", folder_path=folder_path)
    os.makedirs(s.final_tmp_dir(), exist_ok=True)
    open(os.path.join(s.final_tmp_dir(), f"{session_id}.part"), "wb").close()
    return {"session_id": session_id, "chunk_size": 8 * 1024 * 1024, "offset": 0}


@router.patch("/upload/session/{session_id}")
async def upload_chunk(
    session_id: str,
    request: Request,
    key=Depends(get_api_key),
    db=Depends(get_db),
):
    require_scope(key, "write")
    s = get_settings()
    repo = UploadSessionRepo(db)
    sess = await repo.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")
    path = os.path.join(s.final_tmp_dir(), f"{session_id}.part")
    raw_offset = request.headers.get("x-offset", "")
    if not raw_offset.lstrip("-").isdigit():
        raise HTTPException(status_code=400, detail="X-Offset must be an integer")
    offset = int(raw_offset)
    cur = int(sess["offset"])
    if offset != cur:
        return JSONResponse(status_code=409, content={"detail": "offset mismatch", "expected_offset": cur})
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty chunk")
    new_off = cur + len(body)
    if new_off > int(sess["size"]):
        raise HTTPException(status_code=413, detail="chunk exceeds declared file size")
    with open(path, "ab") as fh:
        fh.write(body)
    await repo.advance(session_id, new_off)
    done = new_off >= int(sess["size"])
    resp = {"offset": new_off, "completed": done}
    if done:
        file_id = new_id("f")
        backend = (key.get("backend") or "") or (await get_runtime(db, "default_backend")) or get_settings().default_backend
        storage_chat = (key.get("storage_chat") or "").strip()
        folder_path = (sess.get("folder_path") or "").strip()
        folder_id = await FolderRepo(db).resolve_path(folder_path) if folder_path else None
        await FileRepo(db).create(
            file_id, sess["name"], int(sess["size"]), sess["mime"], uploader=f"key:{key['id']}", source="api", backend=backend, folder_id=folder_id
        )
        await db.audit(f"key:{key['id']}", "file.upload", target=file_id, details=f"resumable {int(sess['size'])}B backend={backend} storage_chat={storage_chat or 'default'} folder={folder_path or 'root'}")
        await state.queue.enqueue("upload", {"file_id": file_id, "tmp_path": path, "size": int(sess["size"]), "backend": backend, "storage_chat": storage_chat, "folder_path": folder_path}, 40)
        quota.add(f"key:{key['id']}", int(sess["size"]))
        resp["file_id"] = file_id
        await repo.delete(session_id)
    return resp


@router.get("")
async def list_files(
    limit: int = 50,
    offset: int = 0,
    storage_chat: str = "",
    default_channel: int = 0,
    q: str = "",
    mime: str = "",
    order: str = "date",
    blocked: int = 0,
    principal=Depends(get_admin_or_key),
    db=Depends(get_db),
):
    """List files with panel-grade filters.

    ?storage_chat=@chan / ?default_channel=1 → channel drill-down,
    ?q=name → name search, ?mime=image/ → type filter, ?order=date|size|downloads|name,
    ?blocked=1 → only blocked files. Response carries total for pagination.
    """
    kw = {}
    if storage_chat:
        kw = {"storage_chat": storage_chat}
    elif default_channel:
        from ..core.settings_service import get_runtime

        default_chat = str(await get_runtime(db, "tg_storage_chat") or "").strip()
        kw = {"storage_chat": default_chat, "storage_chat_is_default": True}
    common = dict(q=q.strip(), mime_prefix=mime.strip().lower(), blocked_only=bool(blocked))
    items = await FileRepo(db).list(
        limit=min(limit, 500), offset=offset, order=order if order in ("date", "size", "downloads", "name") else "date", **kw, **common
    )
    total = await FileRepo(db).count(**kw, **common)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


class FileBlockIn(BaseModel):
    blocked: bool


@router.patch("/{file_id}/block")
async def block_file(file_id: str, body: FileBlockIn, db=Depends(get_db), key=Depends(get_api_key)):
    """Ban/unban: blocked files refuse every serving path (panel, presigned,
    share links) with 403 while the telegram copy stays untouched."""
    require_scope(key, "write")
    repo = FileRepo(db)
    rec = await repo.get(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    await repo.set_blocked(file_id, body.blocked)
    await db.audit(f"key:{key['id']}", "file.block" if body.blocked else "file.unblock", target=file_id)
    return {"ok": True, "file_id": file_id, "blocked": body.blocked}


@router.get("/{file_id}/preview")
async def preview_file(
    file_id: str,
    request: Request,
    db=Depends(get_db),
    key=Depends(get_api_key),
):
    """Inline preview stream (Range-capable) for browser-viewable media.
    Blocked files are refused; everything else falls back to attachment.
    """
    require_scope(key, "read")
    rec = await FileRepo(db).get(file_id)
    if not rec or rec["status"] != "ready" or rec.get("deleted_at"):
        raise HTTPException(status_code=404, detail="file not found or not ready")
    if rec.get("blocked"):
        raise HTTPException(status_code=403, detail="file is blocked")
    if not (rec["mime"] or "").lower().startswith(("image/", "video/", "audio/", "application/pdf", "text/")):
        raise HTTPException(status_code=415, detail="no inline preview for this type")
    parts = await FileRepo(db).parts(file_id)
    return await file_response(
        db=db,
        manager=state.manager,
        rec=rec,
        parts=parts,
        range_header=request.headers.get("range"),
        filename=rec["name"],
        mime=rec["mime"] or "application/octet-stream",
        head_only=request.method == "HEAD",
        inline=True,
    )


@router.get("/{file_id}")
async def file_info(file_id: str, db=Depends(get_db), key=Depends(get_api_key)):
    require_scope(key, "read")
    rec = await FileRepo(db).get(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    rec.pop("message_ids", None)
    rec["parts"] = await FileRepo(db).parts(file_id)
    return rec


@router.delete("/{file_id}")
async def delete_file(file_id: str, purge: bool = False, db=Depends(get_db), key=Depends(get_api_key)):
    """Soft-delete (trash) by default; ?purge=true removes from telegram too."""
    require_scope(key, "write")
    rec = await FileRepo(db).get(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    if purge:
        await state.queue.enqueue("delete", {"file_id": file_id}, 10)
        await db.audit(f"key:{key['id']}", "file.delete", target=file_id, details="purge")
        return {"ok": True, "purged": file_id}
    n = await FileRepo(db).soft_delete(file_id)
    if not n:
        raise HTTPException(status_code=409, detail="already trashed")
    await db.audit(f"key:{key['id']}", "file.trash", target=file_id)
    return {"ok": True, "trashed": file_id, "restore": f"/api/v1/files/{file_id}/restore"}


class LinkIn(BaseModel):
    ttl: Optional[int] = None
    one_time: bool = False


@router.post("/{file_id}/link")
async def make_presigned(file_id: str, body: LinkIn, request: Request, db=Depends(get_db), key=Depends(get_api_key)):
    require_scope(key, "read")
    rec = await FileRepo(db).get(file_id)
    if not rec or rec["status"] != "ready":
        raise HTTPException(status_code=404, detail="file not found or not ready")
    base = str(request.base_url).rstrip("/")
    default_ttl = int(await get_runtime(db, "presigned_ttl") or get_settings().presigned_ttl)
    link = make_link(file_id, base_url=base, ttl=body.ttl or default_ttl, one_time=body.one_time)
    await db.audit(f"key:{key['id']}", "link.create", target=file_id, details=f"ttl={body.ttl or 'default'}")
    return link


@router.get("/{file_id}/content")
async def download_content(
    file_id: str,
    request: Request,
    db=Depends(get_db),
    key=Depends(get_api_key),
):
    """Stream with Range support (auth = API key)."""
    require_scope(key, "read")
    rec = await FileRepo(db).get(file_id)
    if not rec or rec["status"] != "ready":
        raise HTTPException(status_code=404, detail="file not found or not ready")
    _blocked_guard(rec)
    parts = await FileRepo(db).parts(file_id)
    resp = await file_response(
        db=db,
        manager=state.manager,
        rec=rec,
        parts=parts,
        range_header=request.headers.get("range"),
        filename=rec["name"],
        mime=rec["mime"] or "application/octet-stream",
        head_only=request.method == "HEAD",
    )
    quota.add(f"key:{key['id']}", int(rec["size"]))
    return resp


# ── advanced share links (slug / password / max downloads) ─────
def _blocked_guard(rec: dict) -> None:
    if rec.get("blocked"):
        raise HTTPException(status_code=403, detail="file is blocked")


def _pwd_hash(pw: str) -> str:
    from ..core.security import key_hash

    return key_hash(pw)


def _slug_ok(slug: str) -> bool:
    import re as _re

    return bool(_re.fullmatch(r"[a-zA-Z0-9_-]{3,64}", slug))


class ShareIn(BaseModel):
    slug: str = ""
    password: str = ""
    max_downloads: int = 0


@router.post("/{file_id}/share")
async def make_share_link(file_id: str, body: ShareIn, request: Request, db=Depends(get_db), key=Depends(get_api_key)):
    """Create a persistent public link: /{slug} (page) + /d/{slug} (download)."""
    require_scope(key, "write")
    from ..core.models import LinkRepo

    rec = await FileRepo(db).get(file_id)
    if not rec or rec["status"] != "ready" or rec.get("deleted_at"):
        raise HTTPException(status_code=404, detail="file not found or not ready")
    slug = body.slug.strip() or new_id("s").replace("_", "")[:12]
    if not _slug_ok(slug):
        raise HTTPException(status_code=400, detail="slug must be 3-64 chars: letters, digits, - _")
    if await LinkRepo(db).get_by_slug(slug):
        raise HTTPException(status_code=409, detail="slug already taken")
    link_id = await LinkRepo(db).create(
        file_id,
        slug,
        pwd_hash=_pwd_hash(body.password) if body.password else "",
        max_downloads=max(0, int(body.max_downloads)),
    )
    await db.audit(f"key:{key['id']}", "link.share", target=file_id, details=slug)
    base = str(request.base_url).rstrip("/")
    return {"slug": slug, "url": f"{base}/{slug}", "download_url": f"{base}/d/{slug}", "protected": bool(body.password), "max_downloads": link_id and int(body.max_downloads or 0)}


class LinkPatch(BaseModel):
    disabled: bool


@router.patch("/{file_id}/links/{link_id}")
async def patch_link(file_id: str, link_id: int, body: LinkPatch, db=Depends(get_db), key=Depends(get_api_key)):
    """Enable/disable a share link without deleting it (quick kill-switch)."""
    require_scope(key, "write")
    from ..core.models import LinkRepo

    repo = LinkRepo(db)
    rows = await repo.list_for_file(file_id)
    if not any(r["id"] == link_id for r in rows):
        raise HTTPException(status_code=404, detail="link not found")
    await repo.set_disabled(link_id, body.disabled)
    await db.audit(f"key:{key['id']}", "link." + ("disable" if body.disabled else "enable"), target=file_id, details=f"link={link_id}")
    return {"ok": True, "link_id": link_id, "disabled": body.disabled}


@router.get("/{file_id}/links")
async def list_links(file_id: str, db=Depends(get_db), key=Depends(get_api_key)):
    require_scope(key, "read")
    from ..core.models import LinkRepo

    return {"items": await LinkRepo(db).list_for_file(file_id)}


@router.delete("/{file_id}/links/{link_id}")
async def delete_link(file_id: str, link_id: int, db=Depends(get_db), key=Depends(get_api_key)):
    require_scope(key, "write")
    from ..core.models import LinkRepo

    n = await LinkRepo(db).delete(link_id)
    if not n:
        raise HTTPException(status_code=404, detail="link not found")
    return {"ok": True}


# ── trash (soft delete + restore) ──────────────────────────────
@router.get("/{file_id}/restore")
async def restore_file(file_id: str, db=Depends(get_db), key=Depends(get_api_key)):
    """GET for panel simplicity; idempotent."""
    require_scope(key, "write")
    n = await FileRepo(db).restore(file_id)
    if not n:
        raise HTTPException(status_code=404, detail="file not found")
    await db.audit(f"key:{key['id']}", "file.restore", target=file_id)
    return {"ok": True}


# ── public download endpoint (presigned) ───────────────────────────
@public.get("/d/{file_id}")
@public.head("/d/{file_id}")
async def public_download(file_id: str, request: Request, db=Depends(get_db)):
    exp = request.query_params.get("exp", "")
    ot = request.query_params.get("ot", "0")
    sig = request.query_params.get("sig", "")
    jti = request.query_params.get("jti", "")
    from ..services.presign import check as presign_check

    ok, err = presign_check(file_id, exp, ot, sig, jti)
    if not ok:
        raise HTTPException(status_code=403, detail=err)
    rec = await FileRepo(db).get(file_id)
    if not rec or rec["status"] != "ready":
        raise HTTPException(status_code=404, detail="file not found or not ready")
    _blocked_guard(rec)
    parts = await FileRepo(db).parts(file_id)
    return await file_response(
        db=db,
        manager=state.manager,
        rec=rec,
        parts=parts,
        range_header=request.headers.get("range"),
        filename=rec["name"],
        mime=rec["mime"] or "application/octet-stream",
        head_only=request.method == "HEAD",
    )


# ── public slug endpoints (share page + download + thumb) ──────
_SLUG_PAGE = """<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{name}</title>
<style>body{{background:#0F172A;color:#F8FAFC;font-family:Vazirmatn,sans-serif;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#1B2336;border:1px solid #475569;border-radius:12px;padding:24px;max-width:640px;
width:calc(100vw - 32px);text-align:center}}
video,img.audio{{max-width:100%;border-radius:8px;background:#000}}
.btn{{display:inline-block;margin-top:16px;padding:12px 24px;background:#22C55E;color:#0F172A;
border-radius:8px;text-decoration:none;font-weight:700}}
.muted{{color:#94A3B8;font-size:13px;margin-top:8px}}</style></head><body><div class="card">
{media}<h2>{name}</h2><div class="muted">{size} · {hits} دانلود</div>
<a class="btn" href="/d/{slug}/dl" download>⬇ دانلود</a></div></body></html>"""


def _render_slug_page(name: str, size: int, hits: int, slug: str, mime: str, thumb: str) -> str:
    media = f'<img src="{thumb}" alt="">' if thumb else ""
    if mime.startswith("video/"):
        media = f'<video controls preload="metadata" src="/d/{slug}/dl"></video>'
    elif mime.startswith("audio/"):
        media = f'<audio class="audio" controls style="width:100%" src="/d/{slug}/dl"></audio>'
    qr_block = (
        f'<details style="margin-top:12px"><summary style="cursor:pointer;color:#94A3B8">QR Code</summary>'
        f'<img src="/{slug}/qr" alt="QR" width="180" height="180" style="margin-top:8px;background:#fff;padding:8px;border-radius:8px"></details>'
    )

    def _human(n: int) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB":
                return f"{n:.1f} {u}" if u != "B" else f"{n} B"
            n /= 1024
        return f"{n} B"

    return _SLUG_PAGE.format(name=name, size=_human(size), hits=hits, slug=slug, media=media).replace(
        '<a class="btn"', qr_block + '<a class="btn"'
    )


async def _check_slug_access(request: Request, db, slug: str):
    """Resolve slug → (link, file row) enforcing password/max-downloads/disabled.
    Password form is returned as an HTMLResponse when required."""
    from fastapi.responses import HTMLResponse
    from ..core.models import LinkRepo

    link = await LinkRepo(db).get_by_slug(slug)
    if not link or link["disabled"]:
        return None, HTMLResponse("<h3>404 — لینک یافت نشد</h3>", status_code=404)
    rec = await FileRepo(db).get(link["file_id"])
    if not rec or rec["status"] != "ready" or rec.get("deleted_at"):
        return None, HTMLResponse("<h3>فایل در دسترس نیست</h3>", status_code=404)
    if rec.get("blocked"):
        return None, HTMLResponse("<h3>این فایل مسدود شده است</h3>", status_code=403)
    if link["max_downloads"] and link["hits"] >= link["max_downloads"]:
        return None, HTMLResponse("<h3>سقف دانلود این لینک پر شده است</h3>", status_code=403)
    if link["pwd_hash"]:
        supplied = request.query_params.get("pw") or request.headers.get("x-link-password") or ""
        if _pwd_hash(supplied) != link["pwd_hash"]:
            form = (
                '<form method="get" style="font-family:sans-serif;max-width:320px;margin:15vh auto;text-align:center">'
                '<h3>این لینک رمز دارد</h3><input name="pw" type="password" placeholder="رمز لینک" '
                'style="width:100%;padding:10px;margin-top:8px">'
                '<button style="width:100%;padding:10px;margin-top:8px">ورود</button></form>'
            )
            return None, HTMLResponse(form, status_code=401)
    return link, rec


@public.get("/{slug}")
async def slug_page(slug: str, request: Request, db=Depends(get_db)):
    """Public file page: player preview + download button (Pixeldrain-style)."""
    from fastapi.responses import HTMLResponse

    link, rec_or_resp = await _check_slug_access(request, db, slug)
    if link is None:
        return rec_or_resp
    rec = rec_or_resp
    thumb = f"/{slug}/thumb" if rec.get("thumb_message_id") else ""
    return HTMLResponse(
        _render_slug_page(rec["name"], int(rec["size"]), int(link["hits"]), slug, rec["mime"] or "", thumb)
    )


@public.get("/d/{slug}/dl")
async def slug_download(slug: str, request: Request, db=Depends(get_db)):
    """Download through the slug link: enforces password + max-downloads."""
    link, rec_or_resp = await _check_slug_access(request, db, slug)
    if link is None:
        return rec_or_resp
    rec = rec_or_resp
    parts = await FileRepo(db).parts(rec["id"])
    from ..core.models import LinkRepo

    await LinkRepo(db).count_hit(link["id"])
    return await file_response(
        db=db,
        manager=state.manager,
        rec=rec,
        parts=parts,
        range_header=request.headers.get("range"),
        filename=rec["name"],
        mime=rec["mime"] or "application/octet-stream",
        head_only=request.method == "HEAD",
    )


@public.get("/{slug}/thumb")
async def slug_thumb(slug: str, request: Request, db=Depends(get_db)):
    """Stream the stored telegram thumbnail for this file."""
    link, rec_or_resp = await _check_slug_access(request, db, slug)
    if link is None:
        return rec_or_resp
    rec = rec_or_resp
    mid = rec.get("thumb_message_id")
    if not mid:
        raise HTTPException(status_code=404, detail="no thumbnail")
    borrowed = await state.manager.acquire("acc")
    backend = borrowed.backend
    chat = rec["storage_chat"] or getattr(backend, "storage_chat", "me")

    async def gen():
        try:
            async for chunk in backend.iter_file(int(mid), chat, start=0, end=None, size=None):
                yield chunk
        finally:
            await state.manager.release(borrowed.key, None)

    from fastapi.responses import StreamingResponse

    return StreamingResponse(gen(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@public.get("/{slug}/qr")
async def slug_qr(slug: str, request: Request, db=Depends(get_db)):
    """QR code (SVG) pointing at this file's public page."""
    link, rec_or_resp = await _check_slug_access(request, db, slug)
    if link is None:
        return rec_or_resp
    import io as _io

    import qrcode
    import qrcode.image.svg

    url = f"{str(request.base_url).rstrip('/')}/{slug}"
    qr = qrcode.QRCode(image_factory=qrcode.image.svg.SvgPathImage, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image()
    data = img.to_string()
    if isinstance(data, bytes):
        data = data.decode()
    return Response(data, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
