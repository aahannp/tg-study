"""Media: Range streaming (local file or Telegram, chunk by chunk) and offline downloads.

Telegram's upload.getFile wants offset and limit divisible by 4096, 1 MiB divisible by
limit, and no chunk crossing a 1 MiB boundary. 512 KiB chunks at offsets rounded down
to 512 KiB satisfy all of that; we trim the surplus bytes on our side.
"""
import asyncio
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from telethon.errors import FileReferenceExpiredError

from . import telegram
from .db import DATA_DIR
from .sync import _resolve

router = APIRouter(prefix="/api/media")

CHUNK = 512 * 1024
OPEN_CAP = 8 * 1024 * 1024  # "bytes=a-" answers at most this much, so seeking stays snappy
MEDIA_DIR = DATA_DIR / "media"
MSG_TTL = 600  # file references expire; browsers fire many Range requests per video


class RangeNotSatisfiable(Exception):
    pass


# ---- pure range math (tests/test_media.py) ----------------------------------

def parse_range(header: str | None, size: int, cap: int | None = None) -> tuple[int, int] | None:
    """Inclusive (start, end) for a single "bytes=" range, or None to send the whole file.

    Malformed or multi-range headers are ignored (None), as RFC 9110 allows.
    `cap` limits open-ended "bytes=a-" ranges to cap bytes.
    """
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    a, sep, b = header[6:].strip().partition("-")
    if not sep or not (a.isdigit() or b.isdigit()) or (a and not a.isdigit()) or (b and not b.isdigit()):
        return None
    if not a:  # suffix: last b bytes
        if int(b) == 0 or size == 0:
            raise RangeNotSatisfiable
        return max(0, size - int(b)), size - 1
    start = int(a)
    if start >= size:
        raise RangeNotSatisfiable
    if b:
        end = int(b)
        if end < start:
            return None
    else:
        end = start + cap - 1 if cap else size - 1
    return start, min(end, size - 1)


def align(start: int, end: int, chunk: int = CHUNK) -> tuple[int, int, int]:
    """(offset, limit, skip): aligned first offset, number of chunks, leading bytes to drop."""
    offset = start - start % chunk
    return offset, (end - offset) // chunk + 1, start - offset


async def trim(chunks, skip: int, length: int):
    """Yield exactly `length` bytes of `chunks` after dropping the first `skip` bytes."""
    async for chunk in chunks:
        piece = chunk[skip:skip + length]
        skip = max(0, skip - len(chunk))
        if piece:
            length -= len(piece)
            yield bytes(piece)
        if length <= 0:
            return


# ---- Telegram message cache ---------------------------------------------------

_messages: dict[tuple[int, int], tuple[float, object]] = {}


async def _message(client, ch: int, msg: int, fresh: bool = False):
    now = time.monotonic()
    hit = _messages.get((ch, msg))
    if hit and not fresh and now - hit[0] < MSG_TTL:
        return hit[1]
    message = await client.get_messages(await _resolve(client, ch), ids=msg)
    if message is None or message.media is None:
        raise HTTPException(404, "Message has no media on Telegram")
    for k in [k for k, (t, _) in _messages.items() if now - t >= MSG_TTL]:
        del _messages[k]
    _messages[(ch, msg)] = (now, message)
    return message


async def _tg_bytes(client, ch: int, msg: int, message, start: int, end: int):
    """Bytes start..end from Telegram; on an expired file reference, refetch once and resume."""
    pos, retried = start, False
    while pos <= end:
        offset, limit, skip = align(pos, end)
        try:
            async with client.iter_download(message.media, offset=offset, request_size=CHUNK, limit=limit) as it:
                async for piece in trim(it, skip, end - pos + 1):
                    pos += len(piece)
                    yield piece
            return
        except FileReferenceExpiredError:
            if retried:
                raise
            retried = True
            message = await _message(client, ch, msg, fresh=True)


# ---- routes -------------------------------------------------------------------

def _row(conn, ch: int, msg: int):
    row = conn.execute(
        "SELECT m.kind, m.mime_type, m.file_size, m.file_name, i.local_path FROM messages m "
        "LEFT JOIN items i ON i.channel_id = m.channel_id AND i.message_id = m.id "
        "WHERE m.channel_id = ? AND m.id = ?", (ch, msg)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown message")
    return row


def _filename(row, msg: int) -> str:
    return row["file_name"] or f"{msg}{mimetypes.guess_extension(row['mime_type'] or '') or ''}"


# Channel files are untrusted: HTML/SVG/JS served inline from our origin could script /api.
SAFE_INLINE = ("video/", "audio/", "application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp")


def _safe_headers(row, msg: int) -> tuple[str, dict]:
    """(media type, headers): known-safe types inline, everything else a sandboxed octet-stream download."""
    mime = (row["mime_type"] or "").strip().lower()
    headers = {"X-Content-Type-Options": "nosniff"}
    # bare type/subtype only: "image/png, text/html" or parameters could parse differently in a browser
    if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*", mime) and mime.startswith(SAFE_INLINE):
        kind = "inline"
    else:
        mime, kind = "application/octet-stream", "attachment"
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    headers["Content-Disposition"] = f"{kind}; filename*=UTF-8''{quote(_filename(row, msg))}"
    return mime, headers


@router.get("/{ch}/{msg}")
async def media(ch: int, msg: int, request: Request):
    row = _row(request.app.state.conn, ch, msg)
    mime, disposition = _safe_headers(row, msg)
    if row["local_path"] and Path(row["local_path"]).is_file():
        return FileResponse(row["local_path"], media_type=mime, headers=disposition)  # handles Range itself

    size = row["file_size"]
    if row["kind"] == "text" or not size:
        raise HTTPException(404, "Message has no downloadable media")
    try:
        rng = parse_range(request.headers.get("range"), size, cap=OPEN_CAP)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    client = await telegram.get_client()
    message = await _message(client, ch, msg)
    start, end = rng or (0, size - 1)
    headers = {**disposition, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_tg_bytes(client, ch, msg, message, start, end),
                             status_code=206 if rng else 200, media_type=mime, headers=headers)


# ---- offline downloads ---------------------------------------------------------

# ponytail: in-memory status, lost on restart; local_path in the DB is the durable truth
_downloads: dict[tuple[int, int], dict] = {}


def _local(conn, ch: int, msg: int) -> str | None:
    row = conn.execute("SELECT local_path FROM items WHERE channel_id = ? AND message_id = ?", (ch, msg)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown item")
    return row["local_path"]


def _status(conn, ch: int, msg: int) -> dict:
    path = _local(conn, ch, msg)
    if path and Path(path).is_file():
        return {"status": "done", "progress": 1.0}
    d = _downloads.get((ch, msg))
    return {"status": d["status"], "progress": d["progress"]} if d else {"status": "none", "progress": 0.0}


async def _download(conn, client, ch: int, msg: int, dest: Path) -> None:
    state = _downloads[(ch, msg)]
    part = dest.with_name(dest.name + ".part")

    def progress(received, total):
        state["progress"] = received / total if total else 0.0

    try:
        message = await _message(client, ch, msg)
        try:
            await client.download_media(message, file=str(part), progress_callback=progress)
        except FileReferenceExpiredError:
            message = await _message(client, ch, msg, fresh=True)
            await client.download_media(message, file=str(part), progress_callback=progress)
        part.rename(dest)
        with conn:
            conn.execute("UPDATE items SET local_path = ? WHERE channel_id = ? AND message_id = ?",
                         (str(dest), ch, msg))
        state.update(status="done", progress=1.0)
    except asyncio.CancelledError:
        part.unlink(missing_ok=True)
        raise
    except Exception:
        part.unlink(missing_ok=True)
        state["status"] = "error"


@router.post("/{ch}/{msg}/download")
async def start_download(ch: int, msg: int, request: Request):
    conn = request.app.state.conn
    current = _status(conn, ch, msg)
    if current["status"] in ("done", "downloading"):
        return current
    row = _row(conn, ch, msg)
    if row["kind"] == "text":
        raise HTTPException(404, "Message has no downloadable media")
    client = await telegram.get_client()
    folder = MEDIA_DIR / str(ch)
    MEDIA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    suffix = Path(_filename(row, msg)).suffix
    state = _downloads[(ch, msg)] = {"status": "downloading", "progress": 0.0}
    state["task"] = asyncio.create_task(_download(conn, client, ch, msg, folder / f"{msg}{suffix}"))
    return {"status": "downloading", "progress": 0.0}


@router.get("/{ch}/{msg}/download")
async def download_status(ch: int, msg: int, request: Request):
    return _status(request.app.state.conn, ch, msg)


@router.delete("/{ch}/{msg}/download")
async def delete_download(ch: int, msg: int, request: Request):
    conn = request.app.state.conn
    path = _local(conn, ch, msg)
    d = _downloads.pop((ch, msg), None)
    if d and not d["task"].done():
        d["task"].cancel()
    if path:
        Path(path).unlink(missing_ok=True)
    with conn:
        conn.execute("UPDATE items SET local_path = NULL WHERE channel_id = ? AND message_id = ?", (ch, msg))
    return {"status": "none"}
