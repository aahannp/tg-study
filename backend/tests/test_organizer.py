"""Organizer tests. Run: python3 -m pytest tests/   or   python3 tests/test_organizer.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import connect  # noqa: E402
from app.organizer import _caption_chapter, classify_special, extract_lecture, organize_channel  # noqa: E402

CH = 1234


def new_db(username=None, ignore_stickers=1):
    conn = connect(":memory:")
    conn.execute(
        "INSERT INTO channels (id, title, username, selected, ignore_stickers) VALUES (?, 'Physics', ?, 1, ?)",
        (CH, username, ignore_stickers),
    )
    return conn


def add(conn, mid, kind="text", text=None, file_name=None, entities=None, pinned=0):
    conn.execute(
        "INSERT INTO messages (channel_id, id, date, text, entities, kind, file_name, is_pinned) "
        "VALUES (?, ?, '2026-01-01T00:00:00Z', ?, ?, ?, ?, ?)",
        (CH, mid, text, json.dumps(entities or []), kind, file_name, pinned),
    )


def folder_of(conn):
    """{message_id: folder name}"""
    rows = conn.execute(
        "SELECT i.message_id, f.name FROM items i JOIN folders f ON f.id = i.folder_id WHERE i.channel_id = ?", (CH,)
    )
    return {r[0]: r[1] for r in rows}


def folder_names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM folders WHERE channel_id = ? ORDER BY position", (CH,))]


def link(mid, channel=CH):
    return f"https://t.me/c/{channel}/{mid}"


def test_index_with_links():
    conn = new_db()
    for mid in (7, 10, 11, 12, 20, 21, 30, 31, 32, 40):
        add(conn, mid, "video", f"Video {mid}")
    index = "\n".join([
        "📚 Master Index",
        "Chapter 1: Kinematics",
        f"Lecture 1 {link(10)}",
        "Lecture 2",  # text_link entity below
        "Chapter 2: Laws of Motion",
        link(20),
        link(21),
        "**Chapter 3: Work & Energy** – Watch",
        f"Lecture 5 {link(30)}",
        f"Lecture 6 {link(32)}",
        f"Bonus {link(40, channel=999)}",  # other channel: ignored
    ])
    ents = [{"type": "text_link", "text": "Lecture 2", "url": link(12)}]
    add(conn, 1, text=index, entities=ents, pinned=1)
    out = organize_channel(conn, CH)
    assert out["mode"] == "index" and out["items"] == 10
    got = folder_of(conn)
    assert got[10] == got[11] == got[12] == "Chapter 1: Kinematics"  # 11 sits between two links
    assert got[20] == got[21] == "Chapter 2: Laws of Motion"
    assert got[30] == got[31] == got[32] == "Chapter 3: Work & Energy"
    assert got[7] == got[40] == "General / Unsorted"
    assert folder_names(conn) == ["Chapter 1: Kinematics", "Chapter 2: Laws of Motion", "Chapter 3: Work & Energy", "General / Unsorted"]
    conf = {r[0]: r[1] for r in conn.execute("SELECT message_id, confidence FROM items")}
    assert (conf[10], conf[11], conf[7]) == (0.95, 0.85, 0.2)
    assert conn.execute("SELECT parse_mode FROM channels").fetchone()[0] == "index"


def test_index_with_username_links():
    conn = new_db(username="physics_pub")
    for mid in (10, 11, 20):
        add(conn, mid, "video", f"Video {mid}")
    add(conn, 30, "video", "Video 30")
    index = "\n".join([
        "1. Kinematics https://t.me/physics_pub/10",
        "2. Laws of Motion https://t.me/Physics_Pub/20",
        "3. Energy https://t.me/physics_pub/30",
    ])
    add(conn, 1, text=index)
    assert organize_channel(conn, CH)["mode"] == "index"
    got = folder_of(conn)
    # chapters with one link each: media after the link belongs to it until the next link
    assert got[10] == got[11] == "1. Kinematics" and got[20] == "2. Laws of Motion" and got[30] == "3. Energy"


def test_index_with_ranges():
    conn = new_db()
    add(conn, 1, text="\n".join([
        "Chapter 1: Kinematics (Lecture 1-5)",
        "Chapter 2: Laws of Motion (L6–L10)",
        "Chapter 3: Energy – Lec 11 to 15",
    ]))
    add(conn, 2, "video", "Lecture 3 - Velocity")
    add(conn, 3, "video", "L7 Newton")
    add(conn, 4, "video", file_name="013_work_done.mp4")
    add(conn, 5, "video", "Lecture 99 - Bonus")
    out = organize_channel(conn, CH)
    assert out["mode"] == "index"
    got = folder_of(conn)
    assert got[2] == "Chapter 1: Kinematics"
    assert got[3] == "Chapter 2: Laws of Motion"
    assert got[4] == "Chapter 3: Energy"
    assert got[5] == "General / Unsorted"
    assert conn.execute("SELECT confidence FROM items WHERE message_id = 2").fetchone()[0] == 0.85


def test_weak_index_falls_back_to_sequential():
    conn = new_db()
    add(conn, 1, text="\n".join(f"Chapter {c} {link(2 * c)} {link(2 * c + 1)}" for c in (1, 2, 3)))
    for mid in list(range(2, 8)) + list(range(10, 30)):  # index maps 6 of 26 media (< 30%)
        add(conn, mid, "video", f"Lecture {mid}")
    add(conn, 9, text="Unit 1")
    out = organize_channel(conn, CH)
    assert out["mode"] == "sequential"
    assert folder_of(conn)[12] == "Unit 1" and folder_of(conn)[2] == "General / Unsorted"


def test_sequential_headers():
    conn = new_db()
    add(conn, 1, "video", "Lecture 1 - Welcome")  # before any header
    add(conn, 2, text="Chapter 1")
    add(conn, 3, "video", "Lec 2")
    add(conn, 4, text="Unit 2:")
    add(conn, 5, "pdf", file_name="notes.pdf")
    add(conn, 6, text="Thermodynamics", entities=[{"type": "bold", "text": "Thermodynamics", "url": None}])
    add(conn, 7, "video", "Lecture 4")
    add(conn, 8, text="CHAPTER 1:")  # repeat header reuses the folder
    add(conn, 9, "video", "Lecture 5")
    add(conn, 10, text="Good luck everyone, see you tomorrow in the live session.")  # not a header
    add(conn, 11, "video", "Lecture 6")
    out = organize_channel(conn, CH)
    assert out == {"mode": "sequential", "folders": 4, "items": 6}
    got = folder_of(conn)
    assert got[1] == "General / Unsorted"
    assert got[3] == got[9] == got[11] == "Chapter 1"
    assert got[5] == "Unit 2"
    assert got[7] == "Thermodynamics"
    assert folder_names(conn) == ["Chapter 1", "Unit 2", "Thermodynamics", "General / Unsorted"]  # unsorted last
    assert conn.execute("SELECT confidence FROM items WHERE message_id = 3").fetchone()[0] == 0.7


def test_caption_chapter_activates_folder():
    conn = new_db()
    add(conn, 1, text="UNIT 2: MOTION")
    add(conn, 2, "video", "Lecture 1 - Speed")
    add(conn, 3, "video", "Chapter 3 | Lecture 3 Laws of motion")
    add(conn, 4, "video", "CHAPTER 3 | L4 Friction")
    add(conn, 5, "video", "Ch 3: Laws of Motion | L5")
    add(conn, 6, "video", "Part 1 of today's class")  # a sentence: no new folder
    add(conn, 7, text="Chapter 4")
    add(conn, 8, "video", "Ch 4 - Heat | L1")  # same chapter as the standalone header
    organize_channel(conn, CH)
    got = folder_of(conn)
    assert got[2] == "UNIT 2: MOTION"
    assert got[3] == got[4] == got[5] == got[6] == "Chapter 3"
    assert got[8] == "Chapter 4"
    assert folder_names(conn) == ["UNIT 2: MOTION", "Chapter 3", "Chapter 4"]

    def cap(text):
        return _caption_chapter({"kind": "video", "text": text})

    assert cap("Ch 3: Laws of Motion | L5") == "Ch 3: Laws of Motion"
    assert cap("Chapter 3 | Lecture 3 Laws of motion") == "Chapter 3"
    assert cap("Chapter 3 Lecture 3") == "Chapter 3"
    assert cap("Module II") == "Module II"
    assert cap("Part II") is None and cap("Lesson 5 | Thermodynamics") is None  # per-video labels
    assert cap("Part 1 of today's class") is None
    assert cap("Chapter 3 is uploaded") is None
    assert cap("UNIT 2: MOTION and heat") == "UNIT 2: MOTION and heat"  # separator + title is kept
    assert cap("Lecture 3 Laws of motion") is None


def test_caption_lesson_and_part_make_no_folders():
    conn = new_db()
    add(conn, 1, text="Chapter 1")
    add(conn, 2, "video", "Lesson 1 | A")
    add(conn, 3, "video", "Lesson 2 | B")
    add(conn, 4, "video", "Part 2 | C")
    add(conn, 5, "video", "Lesson 3 | C")
    organize_channel(conn, CH)
    assert folder_names(conn) == ["Chapter 1"] and set(folder_of(conn).values()) == {"Chapter 1"}
    add(conn, 6, "video", "Unit 4 - Optics")
    add(conn, 7, "video", "Lesson 4 | D")
    organize_channel(conn, CH)
    got = folder_of(conn)
    assert got[6] == got[7] == "Unit 4: Optics" and folder_names(conn) == ["Chapter 1", "Unit 4: Optics"]
    # standalone text headers keep the full keyword list
    add(conn, 8, text="Lesson 5")
    add(conn, 9, "video", "x")
    organize_channel(conn, CH)
    assert folder_of(conn)[9] == "Lesson 5"


def test_heading_styles():
    conn = new_db()
    for mid, (text, ents) in enumerate([
        ("# Optics", []),
        ("Topic: Waves", []),
        ("Part II", []),
        ("Ch-3", []),
        ("MODERN PHYSICS", []),
        ("Newton's Laws Of Motion", []),
    ], start=1):
        add(conn, mid * 10, text=text, entities=ents)
        add(conn, mid * 10 + 1, "video", f"v{mid}")
    organize_channel(conn, CH)
    assert folder_names(conn) == ["Optics", "Topic: Waves", "Part II", "Ch-3", "MODERN PHYSICS", "Newton's Laws Of Motion"]


def test_special_routing():
    conn = new_db()
    add(conn, 1, text="Chapter 1")
    add(conn, 2, "pdf", file_name="DPP 3.pdf")
    add(conn, 3, "pdf", file_name="Weekly Planner.pdf")
    add(conn, 4, "pdf", file_name="cheatsheet.pdf")  # not a worksheet
    add(conn, 5, "video", "Lecture 1")
    organize_channel(conn, CH)
    got = folder_of(conn)
    assert got[2] == "Worksheets & Sheets"
    assert got[3] == "Weekly Planners"
    assert got[4] == got[5] == "Chapter 1"
    kinds = {r[0]: r[1] for r in conn.execute("SELECT name, kind FROM folders")}
    assert kinds["Weekly Planners"] == "planners" and kinds["Worksheets & Sheets"] == "worksheets"
    assert folder_names(conn) == ["Chapter 1", "Weekly Planners", "Worksheets & Sheets"]
    assert conn.execute("SELECT confidence FROM items WHERE message_id = 2").fetchone()[0] == 0.8


def test_classify_special():
    assert classify_special(None, "DPP 3.pdf") == "worksheets"
    assert classify_special("Practice Set 2", None) == "worksheets"
    assert classify_special(None, "Worksheet_4.pdf") == "worksheets"
    assert classify_special("Maths sheet", None) == "worksheets"
    assert classify_special(None, "Weekly_Planner.pdf") == "planners"
    assert classify_special("Time Table for May", None) == "planners"
    assert classify_special(None, "TimeTable.pdf") == "planners"
    assert classify_special("Weekly assignment", None) == "planners"  # planners win
    for none in ("cheatsheet.pdf", "Spreadsheet basics", "Lecture 4", "Newton's laws", "bridgeplanner"):
        assert classify_special(none, None) is None, none


def test_stickers():
    for ignore, expected in ((1, 1), (0, 2)):
        conn = new_db(ignore_stickers=ignore)
        add(conn, 1, "video", "Lecture 1")
        add(conn, 2, "sticker")
        assert organize_channel(conn, CH)["items"] == expected
    # toggling the flag later removes the sticker item again
    conn.execute("UPDATE channels SET ignore_stickers = 1")
    assert organize_channel(conn, CH)["items"] == 1


def test_rerun_keeps_manual_items():
    conn = new_db()
    add(conn, 1, text="Chapter 1")
    add(conn, 2, "video", "Lecture 1 - Intro")
    add(conn, 3, "video", "Lecture 2 - Next")
    organize_channel(conn, CH)
    custom = conn.execute(
        "INSERT INTO folders (channel_id, name, kind, position) VALUES (?, 'My picks', 'custom', 9)", (CH,)
    ).lastrowid
    # a user-made 'unsorted' folder that only holds a manual item must survive too
    keep = conn.execute(
        "INSERT INTO folders (channel_id, name, kind, position) VALUES (?, 'General / Unsorted', 'unsorted', 8)", (CH,)
    ).lastrowid
    conn.execute("UPDATE items SET folder_id = ?, title = 'Renamed', manual = 1 WHERE message_id = 2", (custom,))
    conn.execute("UPDATE items SET folder_id = ?, manual = 1 WHERE message_id = 3", (keep,))
    empty_custom = conn.execute(
        "INSERT INTO folders (channel_id, name, kind, position) VALUES (?, 'Empty custom', 'custom', 10)", (CH,)
    ).lastrowid

    add(conn, 4, text="Chapter 2")
    add(conn, 5, "video", "Lecture 3")
    out = organize_channel(conn, CH)
    row = conn.execute("SELECT folder_id, title, manual FROM items WHERE message_id = 2").fetchone()
    assert tuple(row) == (custom, "Renamed", 1)
    assert conn.execute("SELECT folder_id FROM items WHERE message_id = 3").fetchone()[0] == keep
    assert folder_of(conn)[5] == "Chapter 2"
    names = set(folder_names(conn))
    assert {"My picks", "Empty custom", "General / Unsorted", "Chapter 2"} <= names
    assert "Chapter 1" not in names  # organizer-made and now empty -> deleted
    assert out["items"] == 3
    assert organize_channel(conn, CH) == out  # idempotent


def test_rename_then_resync_keeps_name_and_order():
    conn = new_db()
    add(conn, 1, text="Chapter 1")
    add(conn, 2, "video", "Lecture 1")
    add(conn, 3, text="Chapter 2")
    add(conn, 4, "video", "Lecture 2")
    organize_channel(conn, CH)
    # what PATCH /api/folders does: rename (auto_name stays) and reorder
    conn.execute("UPDATE folders SET name = 'Kinematics', position = 1 WHERE name = 'Chapter 1'")
    conn.execute("UPDATE folders SET position = 0 WHERE name = 'Chapter 2'")
    add(conn, 5, text="Chapter 1")
    add(conn, 6, "video", "Lecture 3")
    add(conn, 7, text="Chapter 3")
    add(conn, 8, "video", "Lecture 4")
    organize_channel(conn, CH)
    got = folder_of(conn)
    assert got[2] == got[6] == "Kinematics"
    assert folder_names(conn) == ["Chapter 2", "Kinematics", "Chapter 3"]  # user's order kept, new one appended


def test_extract_lecture():
    cases = [
        (("Lecture 12 - Newton's Laws", None), (12, "Newton's Laws")),
        (("Lec-12: Optics", None), (12, "Optics")),
        (("L12 Optics", None), (12, "Optics")),
        (("#12 Optics", None), (12, "Optics")),
        (("Class 7", None), (7, "Class 7")),
        (("12. Heat Transfer", None), (12, "Heat Transfer")),
        ((None, "012_title.mp4"), (12, "title")),
        ((None, "Lec_5_Thermo_Dynamics.mp4"), (5, "Thermo Dynamics")),
        (("🔥 **Lecture 3**\nsecond line", None), (3, "Lecture 3")),
        (("Intro to Waves", "L05.mp4"), (5, "Intro to Waves")),
        (("Lecture 9", "Waves_Basics.mp4"), (9, "Waves Basics")),
        ((None, "Notes.pdf"), (None, "Notes")),
        (("Kinematics revision", None), (None, "Kinematics revision")),
    ]
    for (text, fname), want in cases:
        assert extract_lecture(text, fname, 1) == want, (text, fname, extract_lecture(text, fname, 1))
    assert extract_lecture(None, None, 77) == (None, "Message 77")
    assert extract_lecture("Lecture 2", "3.mp4", 3) == (2, "Lecture 2")  # numeric file stem is no title
    assert extract_lecture("Lecture 2", "Kinematics part 1.mp4", 3) == (2, "Kinematics part 1")
    assert extract_lecture("", "", 5)[1] == "Message 5"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
