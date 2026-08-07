from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tracesurface.server.routes.apis import router as apis_router
from tracesurface.server.routes.cdp import router as cdp_router
from tracesurface.server.routes.replays import router as replays_router
from tracesurface.server.routes.secrets import router as secrets_router
from tracesurface.server.routes.stats import router as stats_router
from tracesurface.storage.sqlite.connection import init

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    init()
    app = FastAPI(title="TraceSurface Report", docs_url="/api/docs", redoc_url=None)
    app.include_router(stats_router)
    app.include_router(replays_router)
    app.include_router(apis_router)
    app.include_router(secrets_router)
    app.include_router(cdp_router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        html = STATIC_DIR / "index.html"
        if not html.exists():
            raise HTTPException(500, f"report frontend not found: {html}")
        return FileResponse(str(html))

    return app
