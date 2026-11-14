"""Study: item detail (prev/next/folder PDFs), watch progress, continue-watching, timestamped notes."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api")

AUTO_COMPLETE = 0.9


class ProgressUpdate(BaseModel):
    position_sec: float | None = None
    duration_sec: float | None = None
    completed: bool | None = None


class NoteCreate(BaseModel):
    body: str
    timestamp_sec: float | None = None


class NoteUpdate(BaseModel):
    body: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_item(conn, ch: int, msg: int) -> None:
    if conn.execute("SELECT 1 FROM items WHERE channel_id = ? AND message_id = ?", (ch, msg)).fetchone() is None:
        raise HTTPException(404, "Unknown item")


def item_detail(conn, ch: int, msg: int) -> dict:
    row = conn.execute(
        """SELECT i.channel_id, i.message_id, i.title, i.lecture_no, m.kind, m.mime_type, m.file_name, m.duration,
                  COALESCE(c.subject, c.title) AS subject, i.folder_id, f.name AS folder_name,
                  COALESCE(p.position_sec, 0) AS position_sec, COALESCE(p.completed, 0) AS completed,
                  i.local_path IS NOT NULL AS downloaded
           FROM items i
           JOIN messages m ON m.channel_id = i.channel_id AND m.id = i.message_id
           JOIN folders f ON f.id = i.folder_id
           JOIN channels c ON c.id = i.channel_id
           LEFT JOIN progress p ON p.channel_id = i.channel_id AND p.message_id = i.message_id
           WHERE i.channel_id = ? AND i.message_id = ?""", (ch, msg)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown item")
    siblings = conn.execute(
        """SELECT i.message_id, i.title, m.kind FROM items i
           JOIN messages m ON m.channel_id = i.channel_id AND m.id = i.message_id
           WHERE i.folder_id = ? ORDER BY i.position, i.message_id""", (row["folder_id"],)).fetchall()
    at = [s["message_id"] for s in siblings].index(msg)
    return {**dict(row), "completed": bool(row["completed"]), "downloaded": bool(row["downloaded"]),
            "prev": dict(siblings[at - 1]) if at > 0 else None,
            "next": dict(siblings[at + 1]) if at + 1 < len(siblings) else None,
            "folder_pdfs": [{"message_id": s["message_id"], "title": s["title"]} for s in siblings if s["kind"] == "pdf"]}


@router.get("/items/{ch}/{msg}")
async def item(ch: int, msg: int, request: Request):
    return item_detail(request.app.state.conn, ch, msg)


@router.put("/progress/{ch}/{msg}")
async def put_progress(ch: int, msg: int, body: ProgressUpdate, request: Request):
    conn = request.app.state.conn
    _require_item(conn, ch, msg)
    old = conn.execute("SELECT position_sec, duration_sec, completed FROM progress WHERE channel_id = ? AND message_id = ?",
                       (ch, msg)).fetchone()
    pos = body.position_sec if body.position_sec is not None else (old["position_sec"] if old else 0.0)
    dur = body.duration_sec if body.duration_sec is not None else (old["duration_sec"] if old else None)
    if body.completed is not None:
        done = body.completed
    else:  # auto-sets, never auto-unsets
        done = bool(old and old["completed"]) or bool(dur and pos >= AUTO_COMPLETE * dur)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO progress (channel_id, message_id, position_sec, duration_sec, completed, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (ch, msg, pos, dur, int(done), _now()))
    return {"position_sec": pos, "duration_sec": dur, "completed": done}


@router.get("/continue")
async def continue_watching(request: Request):
    rows = request.app.state.conn.execute(
        """SELECT p.channel_id, p.message_id, i.title, COALESCE(c.subject, c.title) AS subject, m.kind,
                  p.position_sec, p.duration_sec, p.updated_at
           FROM progress p
           JOIN items i ON i.channel_id = p.channel_id AND i.message_id = p.message_id
           JOIN messages m ON m.channel_id = p.channel_id AND m.id = p.message_id
           JOIN channels c ON c.id = p.channel_id
           WHERE p.completed = 0 AND p.position_sec > 0
           ORDER BY p.updated_at DESC LIMIT 12""").fetchall()
    return [dict(r) for r in rows]


def _note(conn, note_id: int) -> dict:
    row = conn.execute("SELECT id, timestamp_sec, body, created_at FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown note")
    return dict(row)


def _body(body: str) -> str:
    if not body.strip():
        raise HTTPException(400, "Note can't be empty")
    return body


@router.get("/notes/{ch}/{msg}")
async def notes(ch: int, msg: int, request: Request):
    conn = request.app.state.conn
    _require_item(conn, ch, msg)
    rows = conn.execute("SELECT id, timestamp_sec, body, created_at FROM notes WHERE channel_id = ? AND message_id = ? "
                        "ORDER BY timestamp_sec IS NULL, timestamp_sec, id", (ch, msg)).fetchall()
    return [dict(r) for r in rows]


@router.post("/notes/{ch}/{msg}")
async def create_note(ch: int, msg: int, body: NoteCreate, request: Request):
    conn = request.app.state.conn
    _require_item(conn, ch, msg)
    with conn:
        note_id = conn.execute("INSERT INTO notes (channel_id, message_id, timestamp_sec, body) VALUES (?, ?, ?, ?)",
                               (ch, msg, body.timestamp_sec, _body(body.body))).lastrowid
    return _note(conn, note_id)


@router.patch("/notes/{note_id}")
async def update_note(note_id: int, body: NoteUpdate, request: Request):
    conn = request.app.state.conn
    _note(conn, note_id)
    with conn:
        conn.execute("UPDATE notes SET body = ? WHERE id = ?", (_body(body.body), note_id))
    return _note(conn, note_id)


@router.delete("/notes/{note_id}")
async def delete_note(note_id: int, request: Request):
    conn = request.app.state.conn
    _note(conn, note_id)
    with conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return {"ok": True}
