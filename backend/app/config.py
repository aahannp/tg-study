"""Telegram credentials in data/.env (API_ID, API_HASH, PHONE). Hand-parsed; chmod 600."""
import os

from .db import DATA_DIR

ENV_PATH = DATA_DIR / ".env"
KEYS = ("API_ID", "API_HASH", "PHONE")


def load() -> dict:
    """Return {API_ID, API_HASH, PHONE} (missing keys omitted)."""
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() in KEYS:
                out[key.strip()] = val.strip().strip("\"'")
    return out


def is_configured() -> bool:
    cfg = load()
    return all(cfg.get(k) for k in KEYS)


def save(api_id: int | str, api_hash: str, phone: str) -> None:
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    body = f"API_ID={api_id}\nAPI_HASH={api_hash}\nPHONE={phone}\n"
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    os.chmod(ENV_PATH, 0o600)  # in case the file pre-existed with looser bits
