from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tracesurface.ai.catalog import model_catalog
from tracesurface.ai.service import AnalysisManager
from tracesurface.ai.settings import InvalidSettingsError
from tracesurface.server.routes.ai import router as ai_router
from tracesurface.server.routes.apis import router as apis_router
from tracesurface.server.routes.cdp import router as cdp_router
from tracesurface.server.routes.replays import router as replays_router
from tracesurface.server.routes.secrets import router as secrets_router
from tracesurface.server.routes.stats import router as stats_router
from tracesurface.storage.sqlite.connection import init

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    init()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Load the provider catalog before accepting report requests.
        model_catalog()
        app.state.ai_manager = AnalysisManager()
        await app.state.ai_manager.start()
        try:
            yield
        finally:
            await app.state.ai_manager.close()

    app = FastAPI(
        title="TraceSurface Report",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(InvalidSettingsError)
    async def invalid_ai_settings(_request, exc: InvalidSettingsError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.include_router(ai_router)
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
