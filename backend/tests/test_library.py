"""Library, study and media endpoints through FastAPI's TestClient on an in-memory DB. No network."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main, telegram  # noqa: E402

CH = 1234


def _seed(conn):
    conn.execute("INSERT INTO channels (id, title, subject, selected) VALUES (?, 'Physics Channel', 'Physics', 1)", (CH,))
    conn.executemany("INSERT INTO folders (id, channel_id, name, kind, position, auto_name) VALUES (?, ?, ?, ?, ?, ?)",
                     [(1, CH, "Chapter 1", "chapter", 0, "Chapter 1"), (2, CH, "Chapter 2", "chapter", 1, "Chapter 2")])
    # folder 1: videos 10, 11, pdf 12, video 13; folder 2: videos 20, 21
    for mid, kind, folder, pos in [(10, "video", 1, 0), (11, "video", 1, 1), (12, "pdf", 1, 2), (13, "video", 1, 3),
                                   (20, "video", 2, 0), (21, "video", 2, 1)]:
        mime = "application/pdf" if kind == "pdf" else "video/mp4"
        conn.execute("INSERT INTO messages (channel_id, id, date, kind, mime_type, file_size, file_name) "
                     "VALUES (?, ?, '2026-01-01T00:00:00Z', ?, ?, 1000, ?)", (CH, mid, kind, mime, f"f{mid}.mp4"))
        conn.execute("INSERT INTO items (channel_id, message_id, folder_id, title, position) VALUES (?, ?, ?, ?, ?)",
                     (CH, mid, folder, f"Lecture {mid}", pos))
    conn.commit()


@pytest.fixture
def client(monkeypatch):
    async def noop():
        pass
    monkeypatch.setattr(telegram, "startup", noop)
    monkeypatch.setattr(telegram, "shutdown", noop)
    real = db.connect
    monkeypatch.setattr(db, "connect", lambda: real(":memory:"))
    with TestClient(main.app, base_url="http://127.0.0.1") as c:
        _seed(c.app.state.conn)
        yield c


def folders(c):
    return {f["name"]: f for f in c.get(f"/api/channels/{CH}/folders").json()}


def ids(folder):
    return [i["message_id"] for i in folder["items"]]


def test_folder_create_rename_reorder_delete(client):
    r = client.post(f"/api/channels/{CH}/folders", json={"name": "Revision"})
    assert r.status_code == 200 and r.json()["kind"] == "custom" and r.json()["position"] == 2
    new_id = r.json()["id"]
    assert client.post(f"/api/channels/{CH}/folders", json={"name": "Revision"}).status_code == 409
    assert client.post("/api/channels/999/folders", json={"name": "X"}).status_code == 404

    r = client.patch("/api/folders/1", json={"name": "Kinematics"})
    assert r.json()["name"] == "Kinematics"
    assert client.app.state.conn.execute("SELECT auto_name FROM folders WHERE id = 1").fetchone()[0] == "Chapter 1"
    assert client.patch("/api/folders/1", json={"name": "Chapter 2"}).status_code == 409

    client.patch(f"/api/folders/{new_id}", json={"position": 0})
    assert [f["name"] for f in client.get(f"/api/channels/{CH}/folders").json()] == ["Revision", "Kinematics", "Chapter 2"]

    assert client.delete("/api/folders/2").json() == {"ok": True}
    fs = folders(client)
    assert "Chapter 2" not in fs and ids(fs["General / Unsorted"]) == [20, 21]
    assert all(i["manual"] for i in fs["General / Unsorted"]["items"])
    assert [f["position"] for f in client.get(f"/api/channels/{CH}/folders").json()] == [0, 1, 2]
    assert client.delete("/api/folders/999").status_code == 404


def test_move_item_between_folders(client):
    r = client.patch(f"/api/items/{CH}/11", json={"folder_id": 2, "position": 1})
    assert r.status_code == 200 and r.json()["folder_id"] == 2
    fs = folders(client)
    assert ids(fs["Chapter 1"]) == [10, 12, 13] and ids(fs["Chapter 2"]) == [20, 11, 21]
    pos = dict(client.app.state.conn.execute("SELECT message_id, position FROM items WHERE folder_id = 1").fetchall())
    assert pos == {10: 0, 12: 1, 13: 2}
    assert [i["manual"] for i in fs["Chapter 2"]["items"]] == [False, True, False]

    client.patch(f"/api/items/{CH}/13", json={"position": 0, "title": "Renamed"})
    fs = folders(client)
    assert ids(fs["Chapter 1"]) == [13, 10, 12] and fs["Chapter 1"]["items"][0]["title"] == "Renamed"
    assert client.patch(f"/api/items/{CH}/999", json={"title": "x"}).status_code == 404
    assert client.patch(f"/api/items/{CH}/10", json={"folder_id": 999}).status_code == 404


def test_item_detail(client):
    d = client.get(f"/api/items/{CH}/11").json()
    assert d["subject"] == "Physics" and d["folder_name"] == "Chapter 1" and d["kind"] == "video"
    assert d["prev"] == {"message_id": 10, "title": "Lecture 10", "kind": "video"}
    assert d["next"]["message_id"] == 12
    assert d["folder_pdfs"] == [{"message_id": 12, "title": "Lecture 12"}]
    assert client.get(f"/api/items/{CH}/10").json()["prev"] is None
    assert client.get(f"/api/items/{CH}/13").json()["next"] is None
    assert client.get(f"/api/items/{CH}/999").status_code == 404


def test_progress_and_continue(client):
    put = lambda mid, **body: client.put(f"/api/progress/{CH}/{mid}", json=body).json()  # noqa: E731
    assert put(10, position_sec=50, duration_sec=100)["completed"] is False
    assert put(10, position_sec=89.9)["completed"] is False
    assert put(10, position_sec=90)["completed"] is True  # 90% of the remembered duration
    assert put(10, position_sec=5)["completed"] is True  # never auto-unsets
    assert put(10, completed=False)["completed"] is False  # explicit does
    put(11, position_sec=10, duration_sec=100)
    put(20, position_sec=10, duration_sec=100)
    put(21, position_sec=0, duration_sec=100)  # not started: excluded
    put(13, position_sec=95, duration_sec=100)  # completed: excluded
    put(10, position_sec=20)  # newest
    assert [r["message_id"] for r in client.get("/api/continue").json()] == [10, 20, 11]
    f1 = folders(client)["Chapter 1"]
    assert (f1["done"], f1["total"]) == (1, 4)
    assert {i["message_id"]: i["position_sec"] for i in f1["items"]}[11] == 10
    assert client.put(f"/api/progress/{CH}/999", json={"position_sec": 1}).status_code == 404


def test_notes_crud(client):
    url = f"/api/notes/{CH}/10"
    a = client.post(url, json={"body": "untimed"}).json()
    client.post(url, json={"body": "late", "timestamp_sec": 120})
    client.post(url, json={"body": "early", "timestamp_sec": 5})
    assert [n["body"] for n in client.get(url).json()] == ["early", "late", "untimed"]
    assert client.patch(f"/api/notes/{a['id']}", json={"body": "edited"}).json()["body"] == "edited"
    assert client.delete(f"/api/notes/{a['id']}").json() == {"ok": True}
    assert len(client.get(url).json()) == 2
    assert client.delete(f"/api/notes/{a['id']}").status_code == 404
    assert client.post(url, json={"body": "  "}).status_code == 400
    assert client.post(f"/api/notes/{CH}/999", json={"body": "x"}).status_code == 404


def test_local_file_range_and_download_state(client, tmp_path):
    f = tmp_path / "10.mp4"
    f.write_bytes(bytes(range(256)) * 4)
    conn = client.app.state.conn
    conn.execute("UPDATE items SET local_path = ? WHERE message_id = 10", (str(f),))
    conn.commit()
    r = client.get(f"/api/media/{CH}/10", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == (bytes(range(256)) * 4)[100:200]
    assert r.headers["content-range"] == "bytes 100-199/1024" and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("inline")
    assert client.get(f"/api/media/{CH}/10").status_code == 200
    assert client.get(f"/api/media/{CH}/10", headers={"Range": "bytes=5000-"}).status_code == 416
    assert client.get(f"/api/media/{CH}/999").status_code == 404

    assert client.get(f"/api/media/{CH}/10/download").json() == {"status": "done", "progress": 1.0}
    assert folders(client)["Chapter 1"]["items"][0]["downloaded"] is True
    assert client.delete(f"/api/media/{CH}/10/download").json() == {"status": "none"}
    assert not f.exists()
    assert client.get(f"/api/media/{CH}/10/download").json() == {"status": "none", "progress": 0.0}


def test_rejects_rebinding_and_cross_site_writes(client):
    assert client.get("/api/continue", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/continue", headers={"Host": "localhost:9999"}).status_code == 200  # any port
    body = {"body": "x"}
    assert client.post(f"/api/notes/{CH}/10", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(f"/api/notes/{CH}/10", json=body, headers={"Origin": "http://127.0.0.1:9"}).status_code == 403
    assert client.post(f"/api/notes/{CH}/10", json=body, headers={"Origin": "http://127.0.0.1"}).status_code == 200
    assert client.post(f"/api/notes/{CH}/10", json=body).status_code == 200  # no Origin
