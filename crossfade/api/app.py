"""FastAPI application: API routers, live events, security headers, SPA hosting."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..config import DEFAULT_SOURCE, config
from ..db import get_db
from ..worker import get_worker
from . import admin, auth, catalog, history, lines, notifications, preview, services, social
from .deps import current_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_db()
    w = get_worker()
    if config.scheduler:
        w.start_scheduler()
    yield
    w.shutdown()


app = FastAPI(title="Crossfade", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

CSP = ("default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
       "form-action 'self'; "
       # player: 30 s samples from Apple/Spotify, YouTube Music through the privacy-enhanced embed
       "media-src 'self' https://*.itunes.apple.com https://*.mzstatic.com https://*.scdn.co https://*.dzcdn.net; "
       "frame-src https://www.youtube-nocookie.com")


@app.middleware("http")
async def guard(request: Request, call_next):
    # CSRF: state-changing API calls must carry a header browsers never send cross-site without CORS.
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-crossfade") != "1":
            return JSONResponse({"detail": "Invalid request."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for r in (auth.router, admin.router, services.router, lines.router, catalog.router, preview.router, social.router,
          social.invites, notifications.router, history.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    get_db().val("SELECT 1")
    return {"ok": True, "version": config.version, "source": source_link()}


def source_link() -> str:
    """The source of exactly this version when it is an official release, else the repository."""
    if re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", config.version):
        return f"{DEFAULT_SOURCE}/tree/v{config.version}"
    return DEFAULT_SOURCE


@app.get("/api/events")
async def events(request: Request, user: dict = Depends(current_user)):
    """Server-sent events: live progress of the user's line runs."""
    bus = get_worker().bus
    q = bus.subscribe(user["id"])

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(user["id"], q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------- SPA
_index_cache: dict = {}


def _index_csp(index) -> str:
    """CSP for the SPA shell: allow exactly the inline bootstrap script(s) by hash."""
    mtime = index.stat().st_mtime
    if _index_cache.get("mtime") != mtime:
        html = index.read_text(encoding="utf-8")
        hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(m.encode()).digest()).decode() + "'"
                  for m in re.findall(r"<script>(.*?)</script>", html, flags=re.S)]
        _index_cache.update(mtime=mtime, csp=CSP.replace("script-src 'self'", "script-src 'self' " + " ".join(hashes)))
    return _index_cache["csp"]


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found."}, status_code=404)
    root = config.web_dir.resolve()
    if path:
        f = (root / path).resolve()
        if root in f.parents and f.is_file():
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/_app/immutable/" in f"/{path}" else {}
            return FileResponse(f, headers=headers)
    index = root / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "Frontend not built. Run `npm run build` in web/."}, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache", "Content-Security-Policy": _index_csp(index)})
