"""FastAPI app. Run: uvicorn app.main:app --host 127.0.0.1 --port 8765 (loopback only!)."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware
from telethon.errors import RPCError

from . import db, telegram
from .routes_auth import router as auth_router
from .routes_channels import router as channels_router
from .routes_library import router as library_router
from .routes_media import router as media_router
from .routes_study import router as study_router

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.conn = db.connect()
    await telegram.startup()
    yield
    await telegram.shutdown()
    app.state.conn.close()


class SameOriginWrites:
    """Refuse cross-site writes to /api: a web page in the browser could otherwise POST to us.

    Pure ASGI (not BaseHTTPMiddleware) so media streams still see client disconnects.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS") \
                and scope["path"].startswith("/api/"):
            h = Headers(scope=scope)
            origin = h.get("origin")
            if origin is not None and urlsplit(origin).netloc != h.get("host"):
                return await JSONResponse({"detail": "Cross-origin request refused"}, 403)(scope, receive, send)
        await self.app(scope, receive, send)


app = FastAPI(title="TG Study", lifespan=lifespan)
app.add_middleware(SameOriginWrites)
# DNS rebinding: a hostile domain resolving to 127.0.0.1 arrives with its own Host header. Port is ignored.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.exception_handler(RPCError)
async def rpc_error(_: Request, e: RPCError):
    # e.g. PHONE_CODE_INVALID, PASSWORD_HASH_INVALID, FLOOD_WAIT_x: safe to show, carries no secrets
    return JSONResponse({"detail": e.message}, status_code=400)


app.include_router(auth_router)
app.include_router(channels_router)
app.include_router(library_router)
app.include_router(media_router)
app.include_router(study_router)
FRONTEND.mkdir(exist_ok=True)  # StaticFiles errors on a missing dir
# Last: a "/" mount would shadow routes registered after it.
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
