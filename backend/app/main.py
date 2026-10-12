"""Cinelume FastAPI application.

Run with a single worker only (job state lives in this process):

    uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import cover as cover_api
from .api import fonts as fonts_api
from .api import jobs as jobs_api
from .api import models as models_api
from .api import music as music_api
from .api import options as options_api
from .api import system as system_api
from .api import uploads as uploads_api
from .core.config import Settings, get_settings
from .core.errors import PipelineError
from .core.housekeeping import housekeeping_loop
from .core.logging import get_logger, setup_logging
from .deps import set_db, set_manager
from .pipeline.orchestrator import JobManager
from .storage.jobs_db import JobsDB

log = get_logger("cinelume")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _require_tools(settings: Settings) -> None:
    missing = []
    if not settings.resolve_executable(settings.ffmpeg_path):
        missing.append("FFmpeg")
    if not settings.resolve_executable(settings.ffprobe_path):
        missing.append("ffprobe")
    if missing:
        raise RuntimeError(
            "Missing required tool(s): "
            + ", ".join(missing)
            + ". Install a full FFmpeg build (winget install Gyan.FFmpeg) and put it on PATH, "
            "or set FFMPEG_PATH / FFPROBE_PATH in .env."
        )


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    settings.ensure_dirs()
    _require_tools(settings)

    db = JobsDB(settings.data_dir / "jobs.db")
    set_db(db)
    manager = JobManager(db, settings)
    await manager.start()
    set_manager(manager)
    app.state.manager = manager

    housekeeping = asyncio.create_task(housekeeping_loop(db, settings))
    log.info(f"Cinelume backend ready (workers={manager.workers})")
    try:
        yield
    finally:
        housekeeping.cancel()
        await asyncio.gather(housekeeping, return_exceptions=True)
        await manager.shutdown()
        db.close()
        log.info("Cinelume backend stopped")


app = FastAPI(title="Cinelume", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PipelineError)
async def pipeline_error_handler(request: Request, exc: PipelineError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "BAD_REQUEST",
            "message": "Validation failed.",
            "stage": None,
            "errors": jsonable_encoder(exc.errors()),
        },
    )


@app.middleware("http")
async def access_token_middleware(request: Request, call_next):
    settings = get_settings()
    if settings.access_token and request.url.path.startswith("/api"):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {settings.access_token}":
            return JSONResponse(
                status_code=401,
                content={"code": "UNAUTHORIZED", "message": "Missing or invalid access token."},
            )
    return await call_next(request)


app.include_router(system_api.router)
app.include_router(options_api.router)
app.include_router(models_api.router)
app.include_router(music_api.router)
app.include_router(cover_api.router)
app.include_router(uploads_api.router)
app.include_router(jobs_api.router)
app.include_router(fonts_api.router)

_frontend_dist = _REPO_ROOT / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
else:

    @app.get("/")
    def root() -> dict:
        return {"app": "Cinelume", "version": __version__, "status": "ok"}
