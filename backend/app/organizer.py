"""Content segregation engine (Phase 2).

organize_channel(conn, channel_id) reads every row in `messages` for the channel and
(re)builds `folders` + `items`. It is safe to re-run after every incremental sync and
never touches items with manual = 1.

Everything above organize_channel is pure (dicts in, tuples out); the DB is only touched
by organize_channel. Message dicts: id, text, entities (list), kind, file_name, is_pinned.
"""
import json
import re
import sqlite3
import unicodedata
from bisect import bisect_left, bisect_right

MEDIA_KINDS = {"video", "pdf", "image", "document"}
UNSORTED = ("General / Unsorted", "unsorted")
SPECIAL = {"planners": "Weekly Planners", "worksheets": "Worksheets & Sheets"}
CONF_LINK, CONF_RANGE, CONF_HEADER, CONF_SPECIAL, CONF_UNSORTED = 0.95, 0.85, 0.7, 0.8, 0.2
MIN_MAPPED = 0.3  # index must place at least this share of (non-special) media

# Letters-only boundaries: '_', digits and spaces separate words ("Weekly_Planner", "DPP3"),
# but "cheatsheet" does not match "sheet".
_A, _Z = r"(?<![A-Za-z])", r"(?![A-Za-z])"
PLANNER_RE = re.compile(_A + r"(?:planners?|schedules?|timetables?|time[\s_-]?tables?|weekly)" + _Z, re.I)
WORKSHEET_RE = re.compile(_A + r"(?:worksheets?|dpps?|assignments?|sheets?|practice[\s_-]sets?)" + _Z, re.I)

KW = r"(?:chapter|chap|ch|unit|module|part|topic|section|lesson)"
HEAD_NUM = re.compile(rf"^{KW}\s*[-–—.:#]?\s*(?:\d+|[ivxlc]+)(?![A-Za-z])", re.I)  # Chapter 3, Ch-3, Part II
HEAD_COLON = re.compile(rf"^{KW}\s*[-–—:]\s*\S", re.I)  # Topic: Optics
HEAD_MD = re.compile(r"^\s*#{1,6}\s+\S")
# captions only: "Lesson 5 | Thermo" / "Part 2 | ..." are per-video labels, not chapters
CAPTION_HEAD = re.compile(r"^(?:chapter|chap|ch|unit|module)\s*[-–—.:#]?\s*(?:\d+|[ivxlc]+)(?![A-Za-z])", re.I)
CHAPTER_START = re.compile(rf"^{KW}(?![A-Za-z])", re.I)
LECTURE_LIKE = re.compile(r"^(?:lectures?|lect|lec|class|l|#)\s*[-_.:]?\s*\d", re.I)
NUMBERED = re.compile(r"^\d{1,3}\s*[.)\-:]\s*\S")
INDEX_TITLE = re.compile(r"\b(?:index|contents|syllabus|playlist)\b", re.I)
LINK_WORDS = re.compile(r"\b(?:click here|watch(?: now)?|link|here|open|view)\W*$", re.I)

URL_RE = re.compile(r"(?:https?://|t\.me/)\S+", re.I)
# t.me/c/<channel>/<msg>, t.me/<username>/<msg>, optional forum topic id before <msg>
LINK_RE = re.compile(r"t\.me/(c/)?([A-Za-z0-9_]+)/(?:\d+/)?(\d+)", re.I)
_LEC = r"(?:lectures?|lect|lec|class|l)"
RANGE_RE = re.compile(
    rf"(?<![A-Za-z]){_LEC}\.?\s*[-_#:]?\s*(\d{{1,4}})\s*(?:-|–|—|to|~)\s*(?:{_LEC}\.?\s*[-_#:]?\s*)?(\d{{1,4}})(?!\d)",
    re.I,
)

# lecture number patterns, tried in order; the match span is cut out of the title
LECTURE_PATTERNS = [
    re.compile(r"(?<![A-Za-z])(?:lecture|lect|lec|class)\s*[-_.:#]*\s*(\d{1,4})(?!\d)", re.I),
    re.compile(r"(?<![A-Za-z])L[-_]?(\d{1,4})(?!\d)", re.I),
    re.compile(r"#\s*(\d{1,4})(?!\d)"),
    re.compile(r"^\s*(\d{1,3})(?:\s*[._\-)]\s*|\s+-\s*)(?=\S)"),  # "12. Title", "012_title"
]


# ---------- text helpers ----------

def _clean(s):
    """Strip markdown, emoji and extra whitespace from one line."""
    s = re.sub(r"^\s*#{1,6}\s+", "", s or "")  # "# Heading" yes, "#12" no
    s = re.sub(r"[*`~]|__", "", s)
    s = "".join(
        c for c in s
        if unicodedata.category(c) not in ("So", "Cs", "Cn", "Me") and c not in "️‍" and ord(c) <= 0xFFFF
    )
    return " ".join(s.split())


def _first_line(text):
    return next((ln for ln in (text or "").split("\n") if ln.strip()), "")


def _key(name):
    """Folder identity: case/punctuation-insensitive; "Ch 3", "CHAPTER 3" and "Chapter 3: Optics" share one."""
    hit = HEAD_NUM.match(name.strip())
    if hit:
        kw, *num = re.findall(r"[a-z]+|\d+", hit.group(0).casefold())
        return " ".join(["chapter" if kw in ("ch", "chap") else kw] + num)
    return " ".join(re.findall(r"\w+", name.casefold()))


def _entities(raw):
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _is_media(m, ignore_stickers):
    return m["kind"] in MEDIA_KINDS or (m["kind"] == "sticker" and not ignore_stickers)


# ---------- special routing + lecture extraction ----------

def classify_special(text, file_name):
    """'planners' | 'worksheets' | None, from caption + file name. Planners win ties."""
    hay = f"{text or ''} {file_name or ''}"
    if PLANNER_RE.search(hay):
        return "planners"
    if WORKSHEET_RE.search(hay):
        return "worksheets"
    return None


def _tidy(s):
    return " ".join(s.replace("_", " ").split()).strip(" -–—:.|,")


def _split_number(s):
    """(lecture_no|None, remainder) for one cleaned line."""
    for pat in LECTURE_PATTERNS:
        m = pat.search(s)
        if m:
            return int(m.group(1)), _tidy(s[:m.start()] + " " + s[m.end():])
    return None, _tidy(s)


def extract_lecture(text, file_name, message_id=None):
    """(lecture_no, title). Caption's first line wins, file name stem is the fallback."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", file_name or "")
    sources = [s for s in (_clean(_first_line(text)), _clean(stem)) if s]
    number, title = None, None
    for s in sources:
        n, rest = _split_number(s)
        number = n if number is None else number
        if re.search(r"[^\W\d_]", rest):  # needs a letter: "3" or "2024 11 01" from a file name says nothing
            title = title or rest
    # ponytail: "Class 12 Physics" reads as lecture 12; no way to tell a grade from a lecture
    title = title or (_tidy(sources[0]) if sources else "") or file_name or f"Message {message_id}"
    return number, title[:200]


# ---------- Mode A: master index ----------

def _line_links(text, lines, entities):
    """text_link urls per line. Entities carry no offsets, so locate each label in order."""
    starts, pos = [], 0
    for ln in lines:
        starts.append(pos)
        pos += len(ln) + 1
    per_line, cursor = [[] for _ in lines], 0
    for e in entities:
        label = e.get("text") or ""
        if e.get("type") != "text_link" or not e.get("url") or not label:
            continue
        at = text.find(label, cursor)
        if at < 0:
            continue
        cursor = at + len(label)
        per_line[bisect_right(starts, at) - 1].append(e["url"])
    return per_line


def _link_ids(s, channel_id, username):
    """Message ids linked in s that point into this channel (other channels are ignored)."""
    ids = []
    for m in LINK_RE.finditer(s):
        is_private, who, msg = m.groups()
        ours = who == str(channel_id) if is_private else bool(username) and who.lower() == username.lstrip("@").lower()
        if ours:
            ids.append(int(msg))
    return ids


def _index_name(s):
    s = re.sub(r"\(\s*\)|\[\s*\]", "", _clean(s))
    s = LINK_WORDS.sub("", s)
    s = re.sub(r"[\s:.\-–—|>→•(]+$", "", s)
    return re.sub(r"^[\s:.\-–—|>→•)]+", "", s)


def parse_index(msg, channel_id, username):
    """Chapters of an index message: [{name, ids: [msg ids], ranges: [(lo, hi)]}] in order.

    A line starts a chapter if it has a chapter keyword, or carries links/ranges with
    non-lecture text. Lecture-looking lines (or any line under a heading-only chapter)
    attach to the current chapter.
    """
    text = msg["text"] or ""
    lines = text.split("\n")
    bold = {_clean(e.get("text") or "") for e in msg["entities"] if e.get("type") == "bold"} - {""}
    chapters, cur = [], None
    for line, extra in zip(lines, _line_links(text, lines, msg["entities"])):
        ids = _link_ids(" ".join([line] + extra), channel_id, username)
        ranges = [tuple(sorted(map(int, r))) for r in RANGE_RE.findall(line)]
        rest = _index_name(RANGE_RE.sub(" ", URL_RE.sub(" ", line)))
        lecture_like = not rest or bool(LECTURE_LIKE.match(rest))
        if ids or ranges:
            if not CHAPTER_START.match(rest) and cur and (lecture_like or cur["heading"]):
                cur["ids"] += ids
                cur["ranges"] += ranges
            elif CHAPTER_START.match(rest) or not lecture_like:
                cur = {"name": rest, "ids": ids, "ranges": ranges, "heading": False}
                chapters.append(cur)
        elif CHAPTER_START.match(rest) or NUMBERED.match(rest) or (rest in bold and not INDEX_TITLE.search(rest)):
            cur = {"name": rest, "ids": [], "ranges": [], "heading": True}
            chapters.append(cur)
    # ponytail: a bold title line right above a flat link list becomes a chapter that swallows it
    return [{k: c[k] for k in ("name", "ids", "ranges")} for c in chapters if c["ids"] or c["ranges"]]


def find_index(messages, channel_id, username):
    """(index_message, chapters) for the best-scoring candidate, or None."""
    best, best_score, n = None, 0, len(messages)
    for i, m in enumerate(messages):
        if m["kind"] != "text" or not m["text"]:
            continue
        chapters = parse_index(m, channel_id, username)
        if len(chapters) < 3:
            continue
        links = sum(len(c["ids"]) for c in chapters)
        score = links + 2 * len(chapters) + 5 * bool(m["is_pinned"]) + 3 * (i < 10 or i >= n - 10)
        if score > best_score:
            best, best_score = (m, chapters), score
    # ponytail: one index message only; an index split across several messages is not merged
    return best


def index_assign(chapters, media, source_id):
    """(folders, placed): placed = {msg_id: (chapter name, confidence)}.

    Order: linked -> unlinked between two links of one chapter (or after the link of a
    chapter that has a single link, i.e. "start here" style) -> lecture-number range.
    """
    owner = {}
    for i, c in enumerate(chapters):
        for mid in c["ids"]:
            owner.setdefault(mid, i)
    linked = sorted(owner)
    placed = {}
    for m in media:
        mid, ci, conf = m["id"], owner.get(m["id"]), CONF_LINK
        if ci is None:
            p = bisect_left(linked, mid)
            prev = owner[linked[p - 1]] if p else None
            nxt = owner[linked[p]] if p < len(linked) else None
            if prev is not None and (prev == nxt or len(set(chapters[prev]["ids"])) == 1):
                ci, conf = prev, CONF_RANGE  # inferred, so scored like a range
        if ci is None:
            n = extract_lecture(m["text"], m["file_name"])[0]
            ci = next((j for j, c in enumerate(chapters) if n is not None and any(lo <= n <= hi for lo, hi in c["ranges"])), None)
            conf = CONF_RANGE
        if ci is not None:
            placed[mid] = (chapters[ci]["name"], conf)
    return [(c["name"], source_id) for c in chapters], placed


# ---------- Mode B: sequential headers ----------

def _header_name(m):
    """Chapter/topic name if this text-only message is a header, else None."""
    text = (m["text"] or "").strip()
    if m["kind"] != "text" or not text or len(text) > 200 or URL_RE.search(text):
        return None
    line = _first_line(text)
    name = _clean(line).strip(" :.-–—|")
    if not name or len(name) > 100:
        return None
    # ponytail: "Part 1 is uploaded" would still pass as a header; no sentence parsing
    if HEAD_NUM.match(name) or HEAD_COLON.match(name) or HEAD_MD.match(line):
        return name
    # heading-style: short single line, not a sentence, mostly bold / ALL CAPS / Title Case
    words = name.split()
    if "\n" in text or len(name) > 60 or len(words) > 8 or name.endswith((".", "?", "!", ",")):
        return None
    bold = sum(len(e.get("text") or "") for e in m["entities"] if e.get("type") == "bold")
    letters = [c for c in name if c.isalpha()]
    all_caps = name.isupper() and len(letters) >= 3
    # ponytail: any short Title Case message ("Good Morning") counts; stoplist if it gets noisy
    title_case = len(words) >= 2 and all(w[0].isupper() or w[0].isdigit() or len(w) <= 3 for w in words)
    return name if letters and (bold >= 0.7 * len(text) or all_caps or title_case) else None


CAPTION_STOP = {"of", "is", "are", "was", "were", "will", "has", "have", "and", "the", "to", "in", "on", "at", "for"}
_SEPARATED = re.compile(r"\s*[|–—:\-]")
_SPLIT = re.compile(r"\s*[|–—:]\s*|\s+-\s+")


def _caption_chapter(m):
    """Folder name if a media caption opens with a strict chapter token ("Chapter 3 | Lecture 3 ..."), else None.

    Only chapter|chap|ch|unit|module + number/roman counts; "Part 1 of today's class" does not. The lecture part
    never goes into the name; a short non-lecture title after a separator is kept.
    """
    if m["kind"] == "text":
        return None
    line = _clean(_first_line(m["text"]))
    hit = CAPTION_HEAD.match(line)
    if not hit:
        return None
    rest = line[hit.end():]
    separated = _SEPARATED.match(rest)
    title = _SPLIT.split(rest.lstrip(" |–—:-"))[0].strip(" .")
    if LECTURE_LIKE.match(title):
        title = ""
    elif title and not separated and (title.split()[0].casefold() in CAPTION_STOP or not title[0].isupper()):
        return None  # "Part 1 of ...", "Chapter 3 is ..." is a sentence, not a header
    # ponytail: no-separator titles only need a capital first word; "Chapter 3 Notes uploaded" passes
    return line[:hit.end()] + (f": {title}" if title and len(title) <= 60 else "")


def sequential_assign(messages, media_ids):
    """(folders, placed): a header (text message or chapter-token caption) activates its folder until the next one."""
    folders, placed, cur = [], {}, None
    for m in messages:
        name = _header_name(m) or _caption_chapter(m)
        if name and not (cur and _key(name) == _key(cur)):
            cur = name
            folders.append((name, m["id"]))
        if m["id"] in media_ids and cur:
            placed[m["id"]] = (cur, CONF_HEADER)
    return folders, placed


# ---------- plan: pure messages -> folders + items ----------

def plan(messages, channel_id, username, ignore_stickers):
    """(mode, folders [(name, kind, source_message_id)], items [dict])."""
    messages = sorted(messages, key=lambda m: m["id"])
    media = [m for m in messages if _is_media(m, ignore_stickers)]
    special = {m["id"]: classify_special(m["text"], m["file_name"]) for m in media}
    rest = [m for m in media if not special[m["id"]]]

    mode, found = "sequential", find_index(messages, channel_id, username)
    if found and rest:
        folders, placed = index_assign(found[1], rest, found[0]["id"])
        if len(placed) / len(rest) >= MIN_MAPPED:
            mode = "index"
    if mode == "sequential":
        folders, placed = sequential_assign(messages, {m["id"] for m in rest})

    meta = {}  # folder key -> (name, kind, source); insertion order = discovery order

    def folder(name, kind, source=None):
        return meta.setdefault(_key(name), (name, kind, source))[0]

    for name, source in folders:
        folder(name, "chapter", source)
    items = []
    for m in media:
        sp = special[m["id"]]
        if sp:
            name, conf = folder(SPECIAL[sp], sp), CONF_SPECIAL
        elif m["id"] in placed:
            name, conf = folder(placed[m["id"]][0], "chapter"), placed[m["id"]][1]
        else:
            name, conf = folder(*UNSORTED), CONF_UNSORTED
        number, title = extract_lecture(m["text"], m["file_name"], m["id"])
        items.append({"message_id": m["id"], "folder": name, "title": title, "lecture_no": number, "confidence": conf})

    used = {it["folder"] for it in items}
    rank = {"planners": 1, "worksheets": 2, "unsorted": 3}  # chapters first, then these
    out = sorted((f for f in meta.values() if f[0] in used), key=lambda f: rank.get(f[1], 0))
    return mode, out, items


# ---------- DB writer ----------

def organize_channel(conn: sqlite3.Connection, channel_id: int) -> dict:
    """Returns a summary like {"mode": "index"|"sequential", "folders": n, "items": n}."""
    ch = conn.execute("SELECT username, ignore_stickers FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if ch is None:
        raise ValueError(f"unknown channel {channel_id}")
    rows = conn.execute(
        "SELECT id, text, entities, kind, file_name, is_pinned FROM messages WHERE channel_id = ? ORDER BY id",
        (channel_id,),
    ).fetchall()
    messages = [dict(r, entities=_entities(r["entities"])) for r in rows]
    mode, folders, items = plan(messages, channel_id, ch["username"], ch["ignore_stickers"])

    # SAVEPOINT = one transaction whether or not the caller already opened one
    conn.execute("SAVEPOINT organizer")
    try:
        # identity is auto_name, so a folder the user renamed keeps filling; fall back to the visible name
        rows = conn.execute("SELECT id, name, auto_name, kind FROM folders WHERE channel_id = ?", (channel_id,)).fetchall()
        by_auto = {r["auto_name"]: r for r in rows if r["auto_name"]}
        by_name = {r["name"]: r for r in rows}
        fid = {}
        for pos, (name, kind, source) in enumerate(folders):
            row = by_auto.get(name) or by_name.get(name)
            if row is None:  # only new folders get a position; existing ones keep the user's order
                conn.execute("UPDATE folders SET position = position + 1 WHERE channel_id = ? AND position >= ?",
                             (channel_id, pos))
                fid[name] = conn.execute(
                    "INSERT INTO folders (channel_id, name, kind, position, source_message_id, auto_name) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (channel_id, name, kind, pos, source, name),
                ).lastrowid
            else:
                fid[name] = row["id"]
                if row["kind"] != "custom":  # a user's folder that shares a name stays as the user made it
                    conn.execute(
                        "UPDATE folders SET kind = ?, source_message_id = ?, auto_name = COALESCE(auto_name, ?) "
                        "WHERE id = ?",
                        (kind, source, name, row["id"]),
                    )

        have = {r["message_id"]: r["manual"] for r in conn.execute("SELECT message_id, manual FROM items WHERE channel_id = ?", (channel_id,))}
        slot = {}
        for it in items:
            folder_id = fid[it["folder"]]
            position = slot[folder_id] = slot.get(folder_id, -1) + 1
            if have.get(it["message_id"]):
                continue  # manual = 1: change nothing
            args = (folder_id, it["title"], it["lecture_no"], position, it["confidence"])
            if it["message_id"] in have:
                conn.execute(
                    "UPDATE items SET folder_id = ?, title = ?, lecture_no = ?, position = ?, confidence = ? "
                    "WHERE channel_id = ? AND message_id = ?",
                    args + (channel_id, it["message_id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO items (folder_id, title, lecture_no, position, confidence, channel_id, message_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    args + (channel_id, it["message_id"]),
                )

        planned = {it["message_id"] for it in items}  # e.g. stickers dropped after a toggle
        conn.executemany(
            "DELETE FROM items WHERE channel_id = ? AND message_id = ?",
            [(channel_id, mid) for mid, manual in have.items() if not manual and mid not in planned],
        )
        # empty organizer-made folders go; custom folders and folders holding any item (incl. manual) stay
        conn.execute(
            "DELETE FROM folders WHERE channel_id = ? AND kind != 'custom' "
            "AND NOT EXISTS (SELECT 1 FROM items WHERE items.folder_id = folders.id)",
            (channel_id,),
        )
        conn.execute("UPDATE channels SET parse_mode = ? WHERE id = ?", (mode, channel_id))
        conn.execute("RELEASE organizer")
    except BaseException:
        conn.execute("ROLLBACK TO organizer")
        conn.execute("RELEASE organizer")
        raise

    count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE channel_id = ?", (channel_id,)).fetchone()[0]
    return {"mode": mode, "folders": count("folders"), "items": count("items")}
