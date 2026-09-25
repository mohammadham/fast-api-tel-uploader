"""Virtual folders (tag-like, nested) for organizing files.

Endpoints (admin JWT or API key):
  GET    /api/v1/folders                       → flat list + tree + path + stats
  POST   /api/v1/folders                       → create (name+parent, or full path)
  PATCH  /api/v1/folders/{id}                  → rename / move
  DELETE /api/v1/folders/{id}                  → delete (files fall back to root)
  GET    /api/v1/folders/{id}/files            → files directly in the folder
  GET    /api/v1/folders/{id}/all              → files in folder + all subfolders
  POST   /api/v1/folders/{id}/files/{file_id}  → move a file into the folder
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.db import Database
from ..core.models import FileRepo, FolderRepo
from ..core.state import get_db
from .deps import get_admin_or_key, require_scope

router = APIRouter(prefix="/api/v1/folders", tags=["folders"])


def _key_row(principal: dict) -> Optional[dict]:
    """API-key row when the caller is a key (JWT admins pass all scopes)."""
    return principal.get("row") if principal.get("type") == "key" else None


def _write_guard(principal: dict) -> None:
    """JWT admins always pass; API keys need the write scope."""
    row = _key_row(principal)
    if row is not None:
        require_scope(row, "write")


def _actor(principal: dict) -> str:
    return principal.get("username") or f"key:{(principal.get('row') or {}).get('id', '?')}"

NAME_MAX = 100
MAX_DEPTH = 32


def _clean_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="folder name required")
    if len(name) > NAME_MAX or "/" in name or "\\" in name or "\0" in name:
        raise HTTPException(status_code=400, detail=f"invalid folder name (max {NAME_MAX} chars, no slashes)")
    if name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid folder name")
    return name


def _clean_path(raw: str) -> List[str]:
    parts = [p.strip() for p in (raw or "").strip().strip("/").split("/")]
    parts = [p for p in parts if p]
    if not parts:
        raise HTTPException(status_code=400, detail="path required")
    if len(parts) > MAX_DEPTH:
        raise HTTPException(status_code=400, detail=f"path too deep (max {MAX_DEPTH} levels)")
    for p in parts:
        _clean_name(p)
    return parts


def _clean_file_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="file name required")
    if len(name) > 255 or "\0" in name:
        raise HTTPException(status_code=400, detail="invalid file name")
    return name


async def _folder_or_404(repo: FolderRepo, folder_id: int) -> dict:
    row = await repo.get(folder_id)
    if not row:
        raise HTTPException(status_code=404, detail="folder not found")
    return row


def _would_cycle(repo: FolderRepo, rows: list, folder_id: int, new_parent: Optional[int]) -> bool:
    """True if making new_parent a child of folder_id (directly or not)."""
    if new_parent is None:
        return False
    by_id = {r["id"]: r for r in rows}
    cur, seen = by_id.get(new_parent), set()
    while cur is not None and cur["id"] not in seen:
        if cur["id"] == folder_id:
            return True
        seen.add(cur["id"])
        cur = by_id.get(cur["parent_id"])
    return False


@router.get("/resolve")
async def resolve_folder(path: str = "", principal=Depends(get_admin_or_key), db: Database = Depends(get_db)):
    """Resolve a nested path to its folder id WITHOUT creating (404 if missing)."""
    repo = FolderRepo(db)
    try:
        fid = await repo.resolve_path(path, create=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if fid is None:
        return {"id": None, "path": ""}
    row = await repo.get(int(fid))
    if not row:
        raise HTTPException(status_code=404, detail="folder not found")
    return {"id": fid, "path": await repo.path_of(fid)}


@router.get("")
async def list_folders(principal=Depends(get_admin_or_key), db: Database = Depends(get_db)):
    repo = FolderRepo(db)
    items = await repo.list()  # includes path + per-folder file stats
    children: dict = {}
    for f in items:
        children.setdefault(f["parent_id"], []).append(f)

    def build(pid: Optional[int]) -> list:
        return [
            {
                "id": f["id"], "name": f["name"], "path": f["path"],
                "file_count": f["file_count"], "total_size": f["total_size"],
                "created_at": f["created_at"], "children": build(f["id"]),
            }
            for f in children.get(pid, [])
        ]

    return {"items": items, "tree": build(None)}


class FolderIn(BaseModel):
    name: str = ""            # single folder name (requires parent_id)
    path: str = ""            # OR full nested path: "projects/2026/reports"
    parent_id: Optional[int] = None


@router.post("", status_code=201)
async def create_folder(body: FolderIn, principal=Depends(get_admin_or_key), db: Database = Depends(get_db)):
    _write_guard(principal)
    repo = FolderRepo(db)
    if body.path:
        parts = _clean_path(body.path)
        joined = "/".join(parts)
        try:
            existing_id = await repo.resolve_path(joined, create=False)
            if existing_id is not None:
                row = await repo.get(int(existing_id))
                return {"id": existing_id, "name": row["name"], "parent_id": row["parent_id"], "created": False}
            folder_id = await repo.resolve_path(joined, create=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = await repo.get(int(folder_id))
        return {"id": folder_id, "name": row["name"], "parent_id": row["parent_id"], "created": True}

    name = _clean_name(body.name)
    if body.parent_id is not None:
        await _folder_or_404(repo, int(body.parent_id))
    existing = await repo.find_child(body.parent_id, name)
    if existing:
        # idempotent: creating the same folder twice returns the existing one
        return {"id": existing["id"], "name": existing["name"], "parent_id": existing["parent_id"], "created": False}
    folder_id = await repo.create(name, body.parent_id)
    return {"id": folder_id, "name": name, "parent_id": body.parent_id, "created": True}


class FolderPatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None  # null = move to root


@router.patch("/{folder_id}")
async def patch_folder(folder_id: int, body: FolderPatch, principal=Depends(get_admin_or_key), db: Database = Depends(get_db)):
    _write_guard(principal)
    repo = FolderRepo(db)
    await _folder_or_404(repo, folder_id)
    if body.name is not None:
        await repo.rename(folder_id, _clean_name(body.name))
    if "parent_id" in body.model_dump(exclude_unset=True):
        new_parent = body.parent_id
        if new_parent == folder_id:
            raise HTTPException(status_code=400, detail="folder cannot be its own parent")
        if new_parent is not None:
            await _folder_or_404(repo, int(new_parent))
            if _would_cycle(repo, await repo.list(), folder_id, int(new_parent)):
                raise HTTPException(status_code=400, detail="cannot move a folder into its own subtree")
        await repo.move(folder_id, new_parent)
    row = await repo.get(folder_id)
    return {"id": folder_id, "name": row["name"], "parent_id": row["parent_id"]}


@router.delete("/{folder_id}")
async def delete_folder(folder_id: int, principal=Depends(get_admin_or_key), db: Database = Depends(get_db)):
    _write_guard(principal)
    repo = FolderRepo(db)
    await _folder_or_404(repo, folder_id)
    await repo.delete(folder_id)  # FK ON DELETE CASCADE removes nested folders
    return {"ok": True}


async def _list_folder_files(
    db: Database,
    folder_id: int,
    include_subfolders: bool,
    limit: int,
    offset: int,
    principal: dict,
) -> dict:
    repo = FolderRepo(db)
    row = await _folder_or_404(repo, folder_id)
    files = await FileRepo(db).list(
        limit=min(max(limit, 1), 500), offset=max(offset, 0),
        folder_id=folder_id, include_subfolders=include_subfolders,
    )
    return {
        "folder": {"id": row["id"], "name": row["name"], "parent_id": row["parent_id"], "path": await repo.path_of(folder_id)},
        "include_subfolders": include_subfolders,
        "count": len(files),
        "items": files,
    }


@router.get("/{folder_id}/files")
async def folder_files(
    folder_id: int,
    limit: int = 50,
    offset: int = 0,
    principal=Depends(get_admin_or_key),
    db: Database = Depends(get_db),
):
    # get_admin_or_key already enforced read scope for API keys
    return await _list_folder_files(db, folder_id, False, limit, offset, principal)


@router.get("/{folder_id}/all")
async def folder_files_recursive(
    folder_id: int,
    limit: int = 50,
    offset: int = 0,
    principal=Depends(get_admin_or_key),
    db: Database = Depends(get_db),
):
    """Files in this folder and every nested subfolder (recursive CTE)."""
    # get_admin_or_key already enforced read scope for API keys
    return await _list_folder_files(db, folder_id, True, limit, offset, principal)


class FileMoveIn(BaseModel):
    file_ids: List[str] = []  # optional batch move support via POST /{id}/files


@router.post("/{folder_id}/files/{file_id}")
async def move_file_into_folder(
    folder_id: int,
    file_id: str,
    principal=Depends(get_admin_or_key),
    db: Database = Depends(get_db),
):
    _write_guard(principal)
    repo = FolderRepo(db)
    await _folder_or_404(repo, folder_id)
    frepo = FileRepo(db)
    rec = await frepo.get(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    await frepo.set_folder(file_id, folder_id)
    await db.audit(_actor(principal), "folder.file_move", target=file_id, details=f"folder={folder_id}")
    return {"ok": True, "file_id": file_id, "folder_id": folder_id}


@router.delete("/{folder_id}/files/{file_id}")
async def remove_file_from_folder(
    folder_id: int,
    file_id: str,
    principal=Depends(get_admin_or_key),
    db: Database = Depends(get_db),
):
    """Detach a file from the folder (file itself is not deleted)."""
    _write_guard(principal)
    frepo = FileRepo(db)
    rec = await frepo.get(file_id)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    if rec["folder_id"] != folder_id:
        raise HTTPException(status_code=400, detail="file is not in this folder")
    await frepo.set_folder(file_id, None)
    await db.audit(_actor(principal), "folder.file_detach", target=file_id, details=f"folder={folder_id}")
    return {"ok": True, "file_id": file_id, "folder_id": None}
