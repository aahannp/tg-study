"""Library edits: create/rename/reorder/delete folders, move/rename items. Item edits set manual = 1."""
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .organizer import UNSORTED
from .routes_channels import folder_dict
from .routes_study import item_detail

router = APIRouter(prefix="/api")


class FolderCreate(BaseModel):
    name: str


class FolderUpdate(BaseModel):
    name: str | None = None
    position: int | None = None


class ItemUpdate(BaseModel):
    folder_id: int | None = None
    position: int | None = None
    title: str | None = None


def _name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(400, "Name can't be empty")
    return name


def _folder(conn, folder_id: int):
    row = conn.execute("SELECT id, channel_id, name, kind, position FROM folders WHERE id = ?", (folder_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown folder")
    return row


def _out(conn, folder_id: int) -> dict:
    return folder_dict(conn, conn.execute("SELECT id, name, kind, position FROM folders WHERE id = ?",
                                          (folder_id,)).fetchone())


def _place(ids: list, key, index: int | None) -> list:
    """ids without key, then key inserted at index (clamped; None = last)."""
    ids = [i for i in ids if i != key]
    ids.insert(len(ids) if index is None else max(0, min(index, len(ids))), key)
    return ids


def _renumber_folders(conn, channel_id: int, ids: list | None = None) -> None:
    if ids is None:
        ids = [r[0] for r in conn.execute("SELECT id FROM folders WHERE channel_id = ? ORDER BY position, id", (channel_id,))]
    conn.executemany("UPDATE folders SET position = ? WHERE id = ?", [(p, i) for p, i in enumerate(ids)])


def _item_ids(conn, folder_id: int) -> list:
    return [r[0] for r in conn.execute("SELECT message_id FROM items WHERE folder_id = ? ORDER BY position, message_id",
                                       (folder_id,))]


def _renumber_items(conn, channel_id: int, folder_id: int, ids: list) -> None:
    conn.executemany("UPDATE items SET folder_id = ?, position = ? WHERE channel_id = ? AND message_id = ?",
                     [(folder_id, p, channel_id, m) for p, m in enumerate(ids)])


@router.post("/channels/{channel_id}/folders")
async def create_folder(channel_id: int, body: FolderCreate, request: Request):
    conn = request.app.state.conn
    if conn.execute("SELECT 1 FROM channels WHERE id = ?", (channel_id,)).fetchone() is None:
        raise HTTPException(404, "Unknown channel")
    try:
        with conn:
            fid = conn.execute(
                "INSERT INTO folders (channel_id, name, kind, position) "
                "VALUES (?, ?, 'custom', (SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE channel_id = ?))",
                (channel_id, _name(body.name), channel_id)).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A folder with that name already exists")
    return _out(conn, fid)


@router.patch("/folders/{folder_id}")
async def update_folder(folder_id: int, body: FolderUpdate, request: Request):
    conn = request.app.state.conn
    f = _folder(conn, folder_id)
    try:
        with conn:
            if body.name is not None:
                # auto_name keeps the organizer's identity; backfill it for folders made before the column existed
                conn.execute("UPDATE folders SET name = ?, auto_name = COALESCE(auto_name, "
                             "CASE WHEN kind != 'custom' THEN name END) WHERE id = ?", (_name(body.name), folder_id))
            if body.position is not None:
                ids = [r[0] for r in conn.execute("SELECT id FROM folders WHERE channel_id = ? ORDER BY position, id",
                                                  (f["channel_id"],))]
                _renumber_folders(conn, f["channel_id"], _place(ids, folder_id, body.position))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A folder with that name already exists")
    return _out(conn, folder_id)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, request: Request):
    conn = request.app.state.conn
    f = _folder(conn, folder_id)
    ch = f["channel_id"]
    items = _item_ids(conn, folder_id)
    with conn:
        if items:
            name, kind = UNSORTED
            target = conn.execute("SELECT id FROM folders WHERE channel_id = ? AND COALESCE(auto_name, name) = ?",
                                  (ch, name)).fetchone()
            if target and target["id"] == folder_id:
                raise HTTPException(400, f"Move the items out of {name} before deleting it")
            target_id = target["id"] if target else conn.execute(
                "INSERT INTO folders (channel_id, name, kind, position, auto_name) "
                "VALUES (?, ?, ?, (SELECT COALESCE(MAX(position), -1) + 1 FROM folders WHERE channel_id = ?), ?)",
                (ch, name, kind, ch, name)).lastrowid
            conn.execute("UPDATE items SET manual = 1 WHERE folder_id = ?", (folder_id,))
            _renumber_items(conn, ch, target_id, _item_ids(conn, target_id) + items)
        conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        _renumber_folders(conn, ch)
    return {"ok": True}


@router.patch("/items/{channel_id}/{message_id}")
async def update_item(channel_id: int, message_id: int, body: ItemUpdate, request: Request):
    conn = request.app.state.conn
    item = conn.execute("SELECT folder_id FROM items WHERE channel_id = ? AND message_id = ?",
                        (channel_id, message_id)).fetchone()
    if item is None:
        raise HTTPException(404, "Unknown item")
    old = item["folder_id"]
    new = old if body.folder_id is None else body.folder_id
    if _folder(conn, new)["channel_id"] != channel_id:
        raise HTTPException(400, "That folder belongs to another channel")
    with conn:
        if body.title is not None:
            conn.execute("UPDATE items SET title = ? WHERE channel_id = ? AND message_id = ?",
                         (_name(body.title), channel_id, message_id))
        if new != old or body.position is not None:
            _renumber_items(conn, channel_id, old, [m for m in _item_ids(conn, old) if m != message_id])
            _renumber_items(conn, channel_id, new, _place(_item_ids(conn, new), message_id, body.position))
        conn.execute("UPDATE items SET manual = 1 WHERE channel_id = ? AND message_id = ?", (channel_id, message_id))
    return item_detail(conn, channel_id, message_id)
