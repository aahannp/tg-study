"""Incremental sync: fetch messages newer than channels.last_message_id, store, organize."""
import asyncio
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException
from telethon.tl.types import PeerChannel, PeerChat

from .organizer import organize_channel
from .telegram import message_to_row

_COLS = ("channel_id", "id", "date", "text", "entities", "kind", "file_name",
         "mime_type", "file_size", "duration", "is_pinned", "grouped_id")
_INSERT = f"INSERT OR REPLACE INTO messages ({','.join(_COLS)}) VALUES ({','.join(':' + c for c in _COLS)})"

_lock = asyncio.Lock()  # ponytail: one sync at a time globally; per-channel locks if it ever matters


async def _resolve(client, channel_id: int):
    """Bare id -> input peer. Telethon needs the access hash cached; dialogs fill that cache."""
    for attempt in (0, 1):
        for peer in (PeerChannel(channel_id), PeerChat(channel_id)):
            try:
                return await client.get_input_entity(peer)
            except ValueError:
                pass
        if attempt == 0:
            await client.get_dialogs()
    raise HTTPException(404, "Channel not found in your Telegram dialogs")


async def sync_channel(conn: sqlite3.Connection, client, channel_id: int) -> dict:
    row = conn.execute("SELECT last_message_id FROM channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown channel: PUT /api/channels/{id} first")
    async with _lock:
        entity = await _resolve(client, channel_id)
        rows = [message_to_row(m, channel_id)
                async for m in client.iter_messages(entity, min_id=row["last_message_id"], reverse=True)
                if m.id and not m.action]  # skip MessageEmpty and service messages
        with conn:
            conn.executemany(_INSERT, rows)
            conn.execute(
                "UPDATE channels SET last_message_id=MAX(last_message_id, ?), last_synced_at=? WHERE id=?",
                (max((r["id"] for r in rows), default=0),
                 datetime.now(timezone.utc).isoformat(), channel_id))
        try:
            organized = organize_channel(conn, channel_id)
        except NotImplementedError:  # organizer not merged yet
            organized = {"mode": None, "folders": None, "items": None}
    return {"new_messages": len(rows), **organized}
