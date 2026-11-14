"""message_to_row mapping. Run: python3 -m pytest  or  python3 tests/test_sync_mapping.py"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon.tl.custom import Message
from telethon.tl.types import (
    Document, DocumentAttributeFilename, DocumentAttributeSticker, DocumentAttributeVideo,
    MessageEntityBold, MessageEntityTextUrl, MessageMediaDocument, PeerChannel,
)

from app.telegram import message_to_row

DATE = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def doc(mime, *attrs, size=1234):
    return MessageMediaDocument(document=Document(
        id=1, access_hash=1, file_reference=b"", date=DATE, mime_type=mime,
        size=size, dc_id=1, attributes=list(attrs)))


def msg(text="", media=None, entities=None, **kw):
    return Message(id=7, peer_id=PeerChannel(99), date=DATE, message=text,
                   media=media, entities=entities, **kw)


def test_video_with_utf16_entities():
    text = "😀 Lecture 1 – Intro"  # emoji = 2 UTF-16 units but 1 Python char
    m = msg(text,
            doc("video/mp4", DocumentAttributeFilename("l1.mp4"),
                DocumentAttributeVideo(duration=3725, w=1280, h=720)),
            [MessageEntityBold(offset=3, length=9),   # "Lecture 1 "
             MessageEntityTextUrl(offset=0, length=2, url="https://x.y")],  # the emoji itself
            pinned=True, grouped_id=55)
    r = message_to_row(m, 99)
    assert r["kind"] == "video" and r["file_name"] == "l1.mp4"
    assert r["duration"] == 3725 and r["file_size"] == 1234 and r["mime_type"] == "video/mp4"
    assert r["is_pinned"] == 1 and r["grouped_id"] == 55 and r["channel_id"] == 99 and r["id"] == 7
    assert r["date"] == "2026-01-02T03:04:05+00:00" and r["text"] == text
    assert json.loads(r["entities"]) == [
        {"type": "bold", "text": "Lecture 1", "url": None},
        {"type": "text_link", "text": "😀", "url": "https://x.y"}]


def test_kinds():
    assert message_to_row(msg("hi"), 1)["kind"] == "text"
    assert message_to_row(msg("", doc("application/pdf")), 1)["kind"] == "pdf"
    assert message_to_row(msg("", doc("application/octet-stream", DocumentAttributeFilename("N.PDF"))), 1)["kind"] == "pdf"
    assert message_to_row(msg("", doc("image/webp")), 1)["kind"] == "image"
    assert message_to_row(msg("", doc("application/zip")), 1)["kind"] == "document"
    assert message_to_row(msg("", doc("video/webm", DocumentAttributeSticker(alt="x", stickerset=None))), 1)["kind"] == "sticker"
    assert message_to_row(msg("", doc("video/mp4")), 1)["kind"] == "video"  # video mime without attribute


if __name__ == "__main__":
    test_video_with_utf16_entities()
    test_kinds()
    print("ok")
