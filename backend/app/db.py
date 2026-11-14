"""SQLite access. One file at tg-study/data/app.db; schema applied idempotently."""
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "app.db"
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with row access by name and FK enforcement. Applies schema."""
    if path != ":memory:":
        DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text())
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that CREATE TABLE IF NOT EXISTS can't add to an existing DB."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(folders)")}
    if "auto_name" not in cols:
        conn.execute("ALTER TABLE folders ADD COLUMN auto_name TEXT")
        conn.commit()
