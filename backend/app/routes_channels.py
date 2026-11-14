from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from telethon.tl.types import Channel, Chat

from . import telegram
from .sync import sync_channel

router = APIRouter(prefix="/api")

_CHANNEL_SQL = """
SELECT c.id, c.title, c.subject, c.ignore_stickers, c.last_synced_at, c.parse_mode,
       (SELECT COUNT(*) FROM messages m WHERE m.channel_id = c.id) AS message_count,
       (SELECT COUNT(*) FROM items i WHERE i.channel_id = c.id) AS item_count
FROM channels c"""


def _channel(conn, channel_id: int) -> dict:
    d = dict(conn.execute(_CHANNEL_SQL + " WHERE c.id = ?", (channel_id,)).fetchone())
    d["ignore_stickers"] = bool(d["ignore_stickers"])
    return d


class ChannelUpdate(BaseModel):
    selected: bool | None = None
    subject: str | None = None
    ignore_stickers: bool | None = None
    title: str | None = None
    username: str | None = None


@router.get("/dialogs")
async def dialogs(request: Request):
    client = await telegram.get_client()
    selected = {r["id"] for r in request.app.state.conn.execute("SELECT id FROM channels WHERE selected=1")}
    out = []
    async for d in client.iter_dialogs():
        e = d.entity
        if isinstance(e, Channel):
            kind = "group" if e.megagroup else "channel"
        elif isinstance(e, Chat):
            kind = "group"
        else:
            continue  # users, bots
        out.append({"id": e.id,  # bare id, matches t.me/c/<id>/<msg>
                    "title": d.name, "username": getattr(e, "username", None),
                    "kind": kind, "selected": e.id in selected})
    return out


@router.get("/channels")
async def channels(request: Request):
    conn = request.app.state.conn
    rows = conn.execute(_CHANNEL_SQL + " WHERE c.selected = 1 ORDER BY c.title").fetchall()
    return [{**dict(r), "ignore_stickers": bool(r["ignore_stickers"])} for r in rows]


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: int, body: ChannelUpdate, request: Request):
    conn = request.app.state.conn
    fields = body.model_dump(exclude_unset=True)
    for k in ("selected", "ignore_stickers"):
        if k in fields:
            fields[k] = int(bool(fields[k]))
    if fields.get("title") is None:
        fields.pop("title", None)  # title is NOT NULL; null means "leave alone"
    with conn:
        conn.execute("INSERT OR IGNORE INTO channels (id, title) VALUES (?, ?)",
                     (channel_id, fields.get("title") or f"Channel {channel_id}"))
        if fields:  # keys come from ChannelUpdate, never from the request body directly
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE channels SET {sets} WHERE id = ?", (*fields.values(), channel_id))
    return _channel(conn, channel_id)


@router.post("/channels/{channel_id}/sync")
async def sync(channel_id: int, request: Request):
    client = await telegram.get_client()
    return await sync_channel(request.app.state.conn, client, channel_id)


_ITEMS_SQL = """
SELECT i.message_id, i.title, i.lecture_no, m.kind, m.file_name, m.duration, i.confidence, i.manual,
       COALESCE(p.position_sec, 0) AS position_sec, COALESCE(p.completed, 0) AS completed,
       i.local_path IS NOT NULL AS downloaded
FROM items i JOIN messages m ON m.channel_id = i.channel_id AND m.id = i.message_id
LEFT JOIN progress p ON p.channel_id = i.channel_id AND p.message_id = i.message_id
WHERE i.folder_id = ? ORDER BY i.position, i.message_id"""


def folder_dict(conn, f) -> dict:
    """One folder row (id, name, kind, position) -> the GET .../folders shape, items included."""
    items = [{**dict(r), "manual": bool(r["manual"]), "completed": bool(r["completed"]),
              "downloaded": bool(r["downloaded"])} for r in conn.execute(_ITEMS_SQL, (f["id"],))]
    return {**dict(f), "done": sum(i["completed"] for i in items), "total": len(items), "items": items}


@router.get("/channels/{channel_id}/folders")
async def folders(channel_id: int, request: Request):
    conn = request.app.state.conn
    return [folder_dict(conn, f) for f in conn.execute(
        "SELECT id, name, kind, position FROM folders WHERE channel_id = ? ORDER BY position, id", (channel_id,))]
