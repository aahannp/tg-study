"""Desktop window: runs the FastAPI app on loopback in a thread, shows it in a native pywebview window.

Run from the venv: python desktop.py

KNOWN RISK (macOS WKWebView): <video> with Range requests is supported natively (the backend
answers 206). PDFs in an <iframe> are rendered by WKWebView's built-in PDFKit viewer and normally
work, but this was NOT verified headlessly; if the player's PDF iframe is blank, fall back to
opening /api/media/{ch}/{msg} in the system browser.
"""
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402
import webview  # noqa: E402

from app.main import app  # noqa: E402

HOST = "127.0.0.1"  # loopback only: the Telegram session gives full account access


def free_port(start: int = 8765) -> int:
    for port in range(start, start + 100):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as uvicorn: ignore TIME_WAIT
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}-{start + 99}")


def start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    # Thread is not a daemon: closing the window calls should_exit and we join it.
    thread = threading.Thread(target=server.run, name="uvicorn")
    thread.start()
    return server, thread


def wait_ready(port: int, thread: threading.Thread, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive():
        try:
            urllib.request.urlopen(f"http://{HOST}:{port}/api/auth/status", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server did not start")


def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=10)


def main() -> None:
    port = free_port()
    base = f"http://{HOST}:{port}/"
    server, thread = start_server(port)
    try:
        wait_ready(port, thread)
        window = webview.create_window("TG Study", base, width=1400, height=900,
                                       resizable=True, min_size=(900, 600))

        def keep_local() -> None:  # ponytail: bounces back after load, add before_load blocking if the flash matters
            url = window.get_current_url() or base
            if not url.startswith(base):
                window.load_url(base)

        window.events.loaded += keep_local
        # private_mode=False + storage_path: localStorage (player prefs etc.) survives restarts
        webview.start(private_mode=False, storage_path=str(ROOT / "data" / "webview"))
    finally:
        stop_server(server, thread)


if __name__ == "__main__":
    main()
