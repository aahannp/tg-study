"""The single Telethon client, the login flow, and message -> `messages` row mapping.

Nothing here logs credentials, OTPs, passwords or session strings (nothing logs at all).
"""
import asyncio
import json
import os
import re
from datetime import timezone

from fastapi import HTTPException
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import MessageMediaWebPage

from . import config
from .db import DATA_DIR

SESSION_PATH = DATA_DIR / "tg.session"

_client: TelegramClient | None = None
_client_creds: tuple | None = None
_bg: set = set()  # keeps disconnect tasks referenced until they finish
_pending: dict = {}  # {"phone": ..., "hash": ...} between /start and /code


# ---- client lifecycle -------------------------------------------------------

def _make_client() -> TelegramClient:
    """Create (lazily, inside the running event loop) the one client from data/.env."""
    global _client, _client_creds
    cfg = config.load()
    if not config.is_configured():
        raise HTTPException(400, "Telegram is not configured: set API_ID, API_HASH and phone first")
    creds = (cfg["API_ID"], cfg["API_HASH"])
    if _client is not None and _client_creds != creds:  # credentials changed: drop the old connection
        task = asyncio.get_running_loop().create_task(_client.disconnect())
        _bg.add(task)
        task.add_done_callback(_bg.discard)
        _client = None
    if _client is None:
        _client = TelegramClient(str(SESSION_PATH), int(cfg["API_ID"]), cfg["API_HASH"])
        _client_creds = creds
    return _client


async def connect() -> TelegramClient:
    client = _make_client()
    if not client.is_connected():
        await client.connect()
        if SESSION_PATH.exists():
            os.chmod(SESSION_PATH, 0o600)
    return client


async def get_client(require_auth: bool = True) -> TelegramClient:
    """Connected client. 400 if not configured, 401 if require_auth and not logged in."""
    try:
        client = await connect()
        if require_auth and not await client.is_user_authorized():
            raise HTTPException(401, "Not logged in to Telegram")
    except ConnectionError as e:
        raise HTTPException(503, f"Cannot reach Telegram: {e}")
    return client


async def startup() -> None:
    """Lifespan hook: connect if configured. Offline at boot is fine; get_client retries."""
    if config.is_configured():
        try:
            await connect()
        except Exception:
            pass


async def shutdown() -> None:
    if _client is not None and _client.is_connected():
        await _client.disconnect()


# ---- login flow -------------------------------------------------------------

async def start_login(api_id: int, api_hash: str, phone: str) -> None:
    config.save(api_id, api_hash, phone)
    client = await get_client(require_auth=False)
    sent = await client.send_code_request(phone)
    _pending.update(phone=phone, hash=sent.phone_code_hash)


async def submit_code(code: str) -> dict:
    if not _pending:
        raise HTTPException(400, "No login in progress: call /api/auth/start first")
    client = await get_client(require_auth=False)
    try:
        await client.sign_in(_pending["phone"], code, phone_code_hash=_pending["hash"])
    except SessionPasswordNeededError:
        return {"password_required": True}
    _pending.clear()
    return {"authorized": True}


async def submit_password(password: str) -> dict:
    client = await get_client(require_auth=False)
    await client.sign_in(password=password)
    _pending.clear()
    return {"authorized": True}


async def status() -> dict:
    if not config.is_configured():
        return {"configured": False, "authorized": False, "me": None}
    client = await get_client(require_auth=False)
    if not await client.is_user_authorized():
        return {"configured": True, "authorized": False, "me": None}
    me = await client.get_me()
    name = " ".join(p for p in (me.first_name, me.last_name) if p)
    return {"configured": True, "authorized": True, "me": {"name": name, "phone": me.phone}}


async def logout() -> None:
    """Revoke the session on Telegram (best effort), delete the local file, keep .env."""
    global _client
    if config.is_configured():
        try:
            client = await connect()
            if await client.is_user_authorized():
                await client.log_out()
            await client.disconnect()
        except Exception:
            pass  # still wipe the local session below
    _client = None
    _pending.clear()
    SESSION_PATH.unlink(missing_ok=True)
    SESSION_PATH.with_name(SESSION_PATH.name + "-journal").unlink(missing_ok=True)


# ---- message -> row ---------------------------------------------------------

_ENTITY_TYPES = {"TextUrl": "text_link", "Url": "url"}


def _entity_type(e) -> str:
    name = type(e).__name__.removeprefix("MessageEntity")
    return _ENTITY_TYPES.get(name) or re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _entities(text: str, entities) -> list:
    """Telegram offsets/lengths are UTF-16 code units, not Python str indices."""
    u16 = text.encode("utf-16-le")
    out = []
    for e in entities or []:
        sub = u16[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le", "replace")
        out.append({"type": _entity_type(e), "text": sub, "url": getattr(e, "url", None)})
    return out


def _kind(msg) -> str:
    if msg.sticker:
        return "sticker"
    mime = (msg.file.mime_type or "") if msg.file else ""
    name = ((msg.file.name or "") if msg.file else "").lower()
    if msg.video or mime.startswith("video/"):
        return "video"
    if mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if msg.photo or mime.startswith("image/"):
        return "image"
    if msg.document:
        return "document"
    if msg.media is None or isinstance(msg.media, MessageMediaWebPage):
        return "text"  # a link preview is still just text
    return "other"  # poll, geo, contact, ...


def message_to_row(msg, channel_id: int) -> dict:
    """Map a Telethon Message to a `messages` table row (see schema.sql)."""
    f = msg.file
    text = msg.message or ""
    return {
        "channel_id": channel_id,
        "id": msg.id,
        "date": msg.date.astimezone(timezone.utc).isoformat(),
        "text": text,
        "entities": json.dumps(_entities(text, msg.entities), ensure_ascii=False),
        "kind": _kind(msg),
        "file_name": f.name if f else None,
        "mime_type": f.mime_type if f else None,
        "file_size": f.size if f else None,
        "duration": int(f.duration) if f and f.duration else None,
        "is_pinned": int(bool(msg.pinned)),
        "grouped_id": msg.grouped_id,
    }
