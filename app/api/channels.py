"""Storage channel registry: add/list/update/delete, connectivity test, and
per-channel JSON backup/restore.

A "channel" is a registered destination chat (supergroup/channel/username)
files are stored in. The panel manages the registry here; the connectivity
test borrows a real backend (account pool first, then bots) and does a tiny
probe upload. Backup serializes every file row stored in the channel (with
message_ids and file_parts) into one JSON manifest and uploads that manifest
*to the channel itself* (self-describing backup, caption marker
``#tgdrive-backup``); the returned message_id is persisted so restore can
re-download it without scanning. Restore reads the manifest back via
iter_file and merges rows with INSERT-OR-IGNORE semantics (existing local
rows always win).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.models import ChannelRepo, FileRepo, FolderRepo, new_id, now
from ..core.state import get_db, state
from .deps import get_current_admin

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])

BACKUP_MARKER = "#tgdrive-backup"
BACKUP_VERSION = 1


class ChannelIn(BaseModel):
    chat: str
    label: str = ""
    kind: str = "storage"  # storage | eitaa


class ChannelUpdate(BaseModel):
    label: Optional[str] = None
    kind: Optional[str] = None


async def _default_chat(db) -> str:
    try:
        from ..core.settings_service import get_runtime

        return str((await get_runtime(db, "tg_storage_chat")) or "").strip()
    except Exception:
        return ""


def _normalize_chat(raw: str) -> str:
    chat = (raw or "").strip().lstrip("@")
    if chat.startswith("-100") and chat[3:].isdigit():
        return chat
    if chat.startswith("-") and chat[1:].isdigit():
        return chat
    if chat.isdigit():
        return f"-100{chat}"
    if not chat or any(c in chat for c in " /\\?#"):
        raise HTTPException(status_code=400, detail="invalid chat: use @username or numeric id")
    return f"@{chat}"


async def _stats_by_chat(db) -> Dict[str, Dict[str, int]]:
    default_chat = await _default_chat(db)
    rows = await db.fetch_all(
        "SELECT COALESCE(NULLIF(storage_chat, ''), ?) AS chat,"
        " COUNT(*) AS n, COALESCE(SUM(size),0) AS b"
        " FROM files WHERE deleted_at IS NULL AND status='ready' GROUP BY 1",
        (default_chat,),
    )
    return {r["chat"] or "": {"files": int(r["n"]), "bytes": int(r["b"])} for r in rows}


@router.get("")
async def list_channels(_: str = Depends(get_current_admin), db=Depends(get_db)):
    items = await ChannelRepo(db).list()
    stats = await _stats_by_chat(db)
    default_chat = await _default_chat(db)
    for it in items:
        s = stats.get(it["chat"] or "", {"files": 0, "bytes": 0})
        it["files"] = s["files"]
        it["bytes"] = s["bytes"]
        it["is_system_default"] = bool(default_chat) and it["chat"] == default_chat
    # the system-default chat may have no registry row; surface its stats
    # explicitly so the panel's virtual row and filter options show real counts.
    # '' (no default set) means per-account/Saved — that bucket is keyed ''
    default_stats = stats.get(default_chat, {"files": 0, "bytes": 0})
    return {"items": items, "default_chat": default_chat, "default_stats": default_stats}


@router.post("")
async def add_channel(body: ChannelIn, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    chat = _normalize_chat(body.chat)
    if body.kind not in ("storage", "eitaa"):
        raise HTTPException(status_code=400, detail="kind must be storage or eitaa")
    repo = ChannelRepo(db)
    if await repo.find_by_chat(chat):
        raise HTTPException(status_code=409, detail="channel already registered")
    cid = await repo.create(chat, (body.label or "").strip(), body.kind)
    await db.audit(admin, "channel.add", target=str(cid), details=chat)
    return {"id": cid, "chat": chat}


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: int, body: ChannelUpdate, admin: str = Depends(get_current_admin), db=Depends(get_db)
):
    repo = ChannelRepo(db)
    if not await repo.get(channel_id):
        raise HTTPException(status_code=404, detail="channel not found")
    if body.kind is not None and body.kind not in ("storage", "eitaa"):
        raise HTTPException(status_code=400, detail="kind must be storage or eitaa")
    await repo.update(channel_id, label=(body.label or "").strip() if body.label is not None else None, kind=body.kind)
    await db.audit(admin, "channel.update", target=str(channel_id))
    return {"ok": True}


@router.post("/{channel_id}/toggle")
async def toggle_channel(channel_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = ChannelRepo(db)
    row = await repo.get(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    enabled = not bool(row["enabled"])
    await repo.set_enabled(channel_id, enabled)
    return {"enabled": enabled}


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    repo = ChannelRepo(db)
    if not await repo.get(channel_id):
        raise HTTPException(status_code=404, detail="channel not found")
    await repo.delete(channel_id)
    await db.audit(admin, "channel.delete", target=str(channel_id))
    return {"ok": True}


# ── connectivity test ────────────────────────────────────────────
def _test_pool(kind: str) -> List[str]:
    """Candidate backend keys for the probe: accounts first, then bots.

    Eitaa-kind channels use the eitaayar pool (send-only; download-test
    impossible there, so a successful send is the pass criterion).
    """
    m = state.manager
    if m is None:
        return []
    if kind == "eitaa":
        return sorted(k for k in m._backends if k.startswith("eit:"))
    acc = sorted(k for k in m._backends if k.startswith("acc:"))
    bot = sorted(k for k in m._backends if k.startswith("bot:"))
    return acc + bot


@router.post("/{channel_id}/test")
async def test_channel(channel_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Send a tiny probe file through each candidate backend until one works."""
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    repo = ChannelRepo(db)
    row = await repo.get(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    chat = row["chat"]
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("tgdrive channel probe")
        last_error = ""
        for key in _test_pool(row["kind"]):
            try:
                async with await state.manager.acquire_key(key, timeout=5.0) as backend:
                    result = await backend.send_document(
                        chat, path, f"probe_{channel_id}.txt", "text/plain", caption="tgdrive-probe"
                    )
                state.manager.release_stats(key)
                await repo.set_status(channel_id, "ok")
                await db.audit(admin, "channel.test", target=str(channel_id), details=key)
                return {"ok": True, "backend": key, "message_id": result["message_id"], "chat": chat}
            except Exception as exc:  # try next backend
                last_error = f"{key}: {exc}"[:280]
        await repo.set_status(channel_id, "error", last_error or "no backend available")
        return {"ok": False, "error": last_error or "no backend available", "chat": chat}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ── backup / restore (JSON manifest uploaded to the channel) ─────
async def _channel_files(db, chat: str, default_chat: str) -> List[Dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM files WHERE deleted_at IS NULL AND (storage_chat=? OR (storage_chat='' AND ?=''))",
        (chat, default_chat),
    )
    if chat == default_chat:
        extra = await db.fetch_all("SELECT * FROM files WHERE deleted_at IS NULL AND storage_chat=''", ())
        seen = {r["id"] for r in rows}
        rows.extend(r for r in extra if r["id"] not in seen)
    return rows


async def _build_manifest(db, row: Dict[str, Any], default_chat: str) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    total_bytes = 0
    for f in await _channel_files(db, row["chat"], default_chat):
        parts = await db.fetch_all(
            "SELECT idx, message_id, size FROM file_parts WHERE file_id=? ORDER BY idx", (f["id"],)
        )
        folder_path = await FolderRepo(db).path_of(f.get("folder_id"))
        total_bytes += int(f["size"] or 0)
        files.append({
            "id": f["id"], "name": f["name"], "size": int(f["size"] or 0),
            "mime": f["mime"], "sha256": f.get("sha256") or "",
            "uploader": f.get("uploader") or "", "source": f.get("source") or "api",
            "status": f["status"], "parts": int(f["parts"] or 1),
            "storage_chat": f["storage_chat"], "message_ids": json.loads(f.get("message_ids") or "[]"),
            "downloads": int(f.get("downloads") or 0), "bytes_served": int(f.get("bytes_served") or 0),
            "blocked": int(f.get("blocked") or 0), "folder_path": folder_path,
            "backend": f.get("backend") or "", "created_at": f["created_at"],
            "ready_at": f.get("ready_at"), "file_parts": parts,
        })
    return {
        "app": "TelegramDrive", "kind": "channel-backup", "version": BACKUP_VERSION,
        "channel": {"chat": row["chat"], "label": row["label"] or "", "kind": row["kind"]},
        "ts": time.time(), "default_chat": default_chat,
        "counts": {"files": len(files), "bytes": total_bytes},
        "files": files,
    }


@router.get("/{channel_id}/backup")
async def backup_channel(channel_id: int, admin: str = Depends(get_current_admin), db=Depends(get_db)):
    """Serialize the channel's file table into a JSON manifest and upload it to
    the channel itself. Returns the manifest summary + stored message_id; the
    panel then downloads it via /content for a local copy."""
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    repo = ChannelRepo(db)
    row = await repo.get(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    if not row["enabled"]:
        raise HTTPException(status_code=400, detail="channel is disabled")
    default_chat = await _default_chat(db)
    manifest = await _build_manifest(db, row, default_chat)
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        size = os.path.getsize(path)
        safe_chat = row["chat"].lstrip("@")
        name = f"tgdrive-backup-{safe_chat}-{int(time.time())}.json"
        last_error = ""
        for key in _test_pool(row["kind"]):
            try:
                async with await state.manager.acquire_key(key, timeout=15.0) as backend:
                    result = await backend.send_document(
                        row["chat"], path, name, "application/json",
                        caption=f"{BACKUP_MARKER} {row['chat']}",
                    )
                message_id = int(result["message_id"])
                await repo.set_backup(
                    channel_id, message_id, bytes=int(result.get("size") or size),
                    files=manifest["counts"]["files"],
                )
                await repo.set_status(channel_id, "ok")
                await db.audit(
                    admin, "channel.backup", target=str(channel_id),
                    details=f"{name} files={manifest['counts']['files']} msg={message_id}",
                )
                return {
                    "ok": True, "message_id": message_id, "backend": key, "name": name, "size": size,
                    "files": manifest["counts"]["files"], "bytes": manifest["counts"]["bytes"],
                }
            except Exception as exc:
                last_error = f"{key}: {exc}"[:280]
        raise HTTPException(status_code=502, detail=f"backup upload failed: {last_error or 'no backend available'}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def _download_manifest(channel_row: Dict[str, Any], message_id: int) -> bytes:
    """Fetch the manifest document bytes back from the channel."""
    m = state.manager
    assert m is not None
    chunks: List[bytes] = []
    key = None
    # prefer the same pool the upload used
    for cand in _test_pool(channel_row["kind"]):
        try:
            async with await m.acquire_key(cand, timeout=10.0) as backend:
                async for chunk in backend.iter_file(message_id, channel_row["chat"], start=0, end=None, size=None):
                    chunks.append(chunk)
            key = cand
            break
        except Exception:
            continue
    if key:
        m.release_stats(key)
    if not chunks:
        raise HTTPException(status_code=502, detail="cannot download backup from channel")
    return b"".join(chunks)


def _find_backup_in_fake_store(chat: str) -> Optional[int]:
    """fake-TG only: locate the newest backup document captioned for THIS chat.

    The upload caption is "#tgdrive-backup @chat" (see backup_channel); captions
    are global in the fake store, so they must be filtered by chat — otherwise
    channel B's restore would silently pick up channel A's manifest.
    """
    try:
        from ..tg.fake import FakeBackend

        wanted = chat.lstrip("@")
        mids = []
        for mid, cap in FakeBackend.CAPTIONS.items():
            if not cap.startswith(BACKUP_MARKER):
                continue
            rest = cap[len(BACKUP_MARKER):].strip()
            if not rest or rest.lstrip("@") == wanted:
                mids.append(mid)
        return max(mids) if mids else None
    except Exception:
        return None


@router.post("/{channel_id}/restore")
async def restore_channel(
    channel_id: int,
    admin: str = Depends(get_current_admin),
    db=Depends(get_db),
    message_id: int = 0,
):
    """Merge a channel backup manifest back into the local DB.

    Source: ``message_id`` param → stored ``last_backup_message_id`` → (fake-TG)
    newest ``#tgdrive-backup`` document in the channel. Rows merge with
    INSERT-OR-IGNORE semantics: existing local rows (by file id) always win.
    """
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    repo = ChannelRepo(db)
    row = await repo.get(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    mid = message_id or (row["last_backup_message_id"] or 0)
    if not mid:
        mid = _find_backup_in_fake_store(row["chat"]) or 0
    if not mid:
        raise HTTPException(
            status_code=400,
            detail="no backup known for this channel — take a backup first (or pass ?message_id=)",
        )
    raw = await _download_manifest(row, int(mid))
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid manifest: {exc}")
    if manifest.get("kind") != "channel-backup":
        raise HTTPException(status_code=400, detail="not a TelegramDrive channel backup")

    files: FileRepo = FileRepo(db)
    folders = FolderRepo(db)
    inserted, skipped, missing_parts = [], [], 0
    for f in manifest.get("files") or []:
        fid = str(f.get("id") or "")
        if not fid or await files.get(fid):
            skipped.append(fid or "?")
            continue
        folder_id = None
        fp = (f.get("folder_path") or "").strip("/")
        if fp:
            try:
                folder_id = await folders.resolve_path(fp, create=True)
            except Exception:
                folder_id = None
        await db.execute(
            "INSERT INTO files(id, name, size, mime, sha256, uploader, source, status, parts,"
            " storage_chat, message_ids, downloads, bytes_served, created_at, ready_at, error,"
            " folder_id, blocked, backend)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid, f.get("name") or fid, int(f.get("size") or 0), f.get("mime") or "application/octet-stream",
                f.get("sha256") or "", f.get("uploader") or "", f.get("source") or "api",
                f.get("status") or "queued", int(f.get("parts") or 1),
                f.get("storage_chat") or "", json.dumps(f.get("message_ids") or []),
                int(f.get("downloads") or 0), int(f.get("bytes_served") or 0),
                float(f.get("created_at") or now()), f.get("ready_at"), "",
                folder_id, int(f.get("blocked") or 0), f.get("backend") or "",
            ),
        )
        for p in f.get("file_parts") or []:
            try:
                await files.add_part(fid, int(p["idx"]), int(p["message_id"]), int(p["size"]))
            except Exception:
                missing_parts += 1
        inserted.append(fid)
    await db.audit(
        admin, "channel.restore", target=str(channel_id),
        details=f"msg={mid} inserted={len(inserted)} skipped={len(skipped)}",
    )
    return {
        "ok": True, "message_id": int(mid), "inserted": len(inserted),
        "skipped": len(skipped), "file_ids": inserted[:50], "missing_parts": missing_parts,
    }


@router.get("/{channel_id}/content")
async def backup_content(channel_id: int, _: str = Depends(get_current_admin), db=Depends(get_db), message_id: int = 0):
    """Download the stored backup manifest as JSON (for a local file copy)."""
    if state.manager is None:
        raise HTTPException(status_code=503, detail="manager not running")
    repo = ChannelRepo(db)
    row = await repo.get(channel_id)
    if not row:
        raise HTTPException(status_code=404, detail="channel not found")
    mid = message_id or (row["last_backup_message_id"] or 0)
    if not mid:
        mid = _find_backup_in_fake_store(row["chat"]) or 0
    if not mid:
        raise HTTPException(status_code=404, detail="no backup stored for this channel")
    raw = await _download_manifest(row, int(mid))
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"stored backup is not valid JSON: {exc}")
