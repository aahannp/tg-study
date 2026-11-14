"""Range math for Telegram streaming. Run: python3 -m pytest tests/"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes_media import CHUNK, OPEN_CAP, RangeNotSatisfiable, align, parse_range, trim  # noqa: E402

SIZE = 3 * CHUNK + 1000  # 3 full chunks and a short last one


def test_parse_range():
    assert parse_range(None, SIZE) is None
    assert parse_range("bytes=0-0", SIZE) == (0, 0)
    assert parse_range("bytes=0-", SIZE) == (0, SIZE - 1)
    assert parse_range("bytes=100-199", SIZE) == (100, 199)
    assert parse_range("bytes=100-999999999", SIZE) == (100, SIZE - 1)  # end clamped
    assert parse_range(f"bytes={SIZE - 1}-", SIZE) == (SIZE - 1, SIZE - 1)  # last byte
    assert parse_range("bytes=-500", SIZE) == (SIZE - 500, SIZE - 1)  # suffix
    assert parse_range("bytes=-999999999", SIZE) == (0, SIZE - 1)
    for ignored in ("bytes=5-1", "bytes=a-b", "items=0-1", "bytes=0-1,5-9", "bytes=-"):
        assert parse_range(ignored, SIZE) is None
    for bad in (f"bytes={SIZE}-", f"bytes={SIZE + 10}-{SIZE + 20}", "bytes=-0"):
        with pytest.raises(RangeNotSatisfiable):
            parse_range(bad, SIZE)


def test_open_range_is_capped():
    assert parse_range("bytes=10-", 100 * OPEN_CAP, cap=OPEN_CAP) == (10, 10 + OPEN_CAP - 1)
    assert parse_range("bytes=10-", 1000, cap=OPEN_CAP) == (10, 999)  # cap past the end: honest end
    assert parse_range("bytes=10-20", 100 * OPEN_CAP, cap=OPEN_CAP) == (10, 20)  # explicit end not capped


def test_align():
    assert align(0, 0) == (0, 1, 0)
    assert align(100, 199) == (0, 1, 100)
    assert align(CHUNK + 7, CHUNK + 9) == (CHUNK, 1, 7)  # mid-chunk
    assert align(10, CHUNK - 1) == (0, 1, 10)  # b on the last byte of a chunk
    assert align(10, CHUNK) == (0, 2, 10)  # b on the first byte of the next chunk
    assert align(SIZE - 1, SIZE - 1) == (3 * CHUNK, 1, SIZE - 1 - 3 * CHUNK)
    for a, b in ((0, 0), (5, 3 * CHUNK), (CHUNK, 2 * CHUNK - 1), (SIZE - 500, SIZE - 1)):
        offset, limit, skip = align(a, b)
        assert offset % 4096 == 0 and offset % CHUNK == 0 and offset + skip == a
        assert offset + limit * CHUNK > b >= offset + (limit - 1) * CHUNK


async def _chunks(data, offset, limit):
    """What iter_download yields: CHUNK-sized pieces from offset, the last one short."""
    for i in range(limit):
        piece = data[offset + i * CHUNK:offset + (i + 1) * CHUNK]
        if piece:
            yield memoryview(piece)


def _read(data, a, b):
    offset, limit, skip = align(a, b)

    async def run():
        return b"".join([p async for p in trim(_chunks(data, offset, limit), skip, b - a + 1)])
    return asyncio.run(run())


def test_trim_matches_slice():
    data = bytes(i % 251 for i in range(SIZE))
    for header in ("bytes=0-0", "bytes=0-", "bytes=100-199", f"bytes={CHUNK - 3}-{CHUNK + 3}",
                   f"bytes={CHUNK + 50}-{2 * CHUNK - 1}", f"bytes={SIZE - 1}-", "bytes=-500"):
        a, b = parse_range(header, SIZE)
        assert _read(data, a, b) == data[a:b + 1], header


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")


def test_untrusted_types_are_sandboxed_downloads():
    from app.routes_media import _safe_headers
    html = {"mime_type": "text/html", "file_name": "x.html"}
    mime, h = _safe_headers(html, 1)
    assert mime == "application/octet-stream" and h["Content-Disposition"].startswith("attachment")
    assert "sandbox" in h["Content-Security-Policy"] and h["X-Content-Type-Options"] == "nosniff"
    assert _safe_headers({"mime_type": "image/svg+xml", "file_name": "a.svg"}, 1)[0] == "application/octet-stream"
    mime, h = _safe_headers({"mime_type": "video/mp4", "file_name": "l.mp4"}, 1)
    assert mime == "video/mp4" and h["Content-Disposition"].startswith("inline")
    assert _safe_headers({"mime_type": "application/pdf", "file_name": "n.pdf"}, 1)[1]["Content-Disposition"].startswith("inline")
    for bad in ("image/png, text/html", "video/mp4;x=<b>", "video/mp4 text/html", "image/png\r\nX: y"):
        assert _safe_headers({"mime_type": bad, "file_name": "f"}, 1)[0] == "application/octet-stream", bad
