-- TG Study — SQLite schema. Applied idempotently by app/db.py on startup.
-- One Telegram channel = one "subject". Folders (chapters/planners/worksheets/unsorted)
-- live under a channel; items place one media message into one folder.

PRAGMA foreign_keys = ON;

-- Channels the user has seen in the picker. Only selected = 1 are synced/organized.
CREATE TABLE IF NOT EXISTS channels (
    id              INTEGER PRIMARY KEY,          -- bare Telegram entity id (matches t.me/c/<id>/<msg> links)
    title           TEXT    NOT NULL,
    username        TEXT,                         -- public @username, NULL for private (matches t.me/<username>/<msg>)
    subject         TEXT,                         -- display name; NULL = use title
    selected        INTEGER NOT NULL DEFAULT 0,
    ignore_stickers INTEGER NOT NULL DEFAULT 1,   -- toggle: drop stickers/spam from organizing
    last_message_id INTEGER NOT NULL DEFAULT 0,   -- incremental-sync cursor: fetch only id > this
    last_synced_at  TEXT,
    parse_mode      TEXT                          -- set by organizer: 'index' | 'sequential'
);

-- Raw cache of every fetched message (text + media metadata, never the media bytes).
CREATE TABLE IF NOT EXISTS messages (
    channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,                 -- Telegram message id (monotonic per channel)
    date        TEXT    NOT NULL,                 -- ISO-8601 UTC
    text        TEXT,                             -- message text or media caption
    entities    TEXT,                             -- JSON list: [{"type":"bold"|"text_link"|"url"|..., "text":"...", "url":null|"..."}]
    kind        TEXT    NOT NULL,                 -- 'text'|'video'|'pdf'|'image'|'sticker'|'document'|'other'
    file_name   TEXT,
    mime_type   TEXT,
    file_size   INTEGER,
    duration    INTEGER,                          -- seconds (video)
    is_pinned   INTEGER NOT NULL DEFAULT 0,
    grouped_id  INTEGER,                          -- album id
    PRIMARY KEY (channel_id, id)
);

-- Folders under a subject. Organizer upserts by (channel_id, name); users may add/rename.
CREATE TABLE IF NOT EXISTS folders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    name              TEXT    NOT NULL,
    kind              TEXT    NOT NULL DEFAULT 'chapter', -- 'chapter'|'planners'|'worksheets'|'unsorted'|'custom'
    position          INTEGER NOT NULL DEFAULT 0,
    source_message_id INTEGER,                    -- header/index message that created it, if any
    auto_name         TEXT,                       -- organizer's name for it; survives user renames (organizer matches on this)
    UNIQUE (channel_id, name)
);

-- A study item: one media message placed in one folder.
CREATE TABLE IF NOT EXISTS items (
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    folder_id   INTEGER NOT NULL REFERENCES folders(id),
    title       TEXT    NOT NULL,
    lecture_no  INTEGER,
    position    INTEGER NOT NULL DEFAULT 0,
    confidence  REAL,                             -- organizer's confidence 0..1
    manual      INTEGER NOT NULL DEFAULT 0,       -- 1 = user moved/renamed it; organizer must never overwrite
    local_path  TEXT,                             -- set when downloaded for offline (Phase 3)
    PRIMARY KEY (channel_id, message_id),
    FOREIGN KEY (channel_id, message_id) REFERENCES messages(channel_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder_id, position);

-- Watch progress (Phase 4). Auto-saved every 5s by the player.
CREATE TABLE IF NOT EXISTS progress (
    channel_id   INTEGER NOT NULL,
    message_id   INTEGER NOT NULL,
    position_sec REAL    NOT NULL DEFAULT 0,
    duration_sec REAL,
    completed    INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel_id, message_id),
    FOREIGN KEY (channel_id, message_id) REFERENCES messages(channel_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_progress_recent ON progress(updated_at DESC);

-- Timestamped notes (Phase 4). timestamp_sec NULL = note not tied to a moment.
CREATE TABLE IF NOT EXISTS notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id    INTEGER NOT NULL,
    message_id    INTEGER NOT NULL,
    timestamp_sec REAL,
    body          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id, message_id) REFERENCES messages(channel_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notes_item ON notes(channel_id, message_id, timestamp_sec);
