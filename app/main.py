"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.llm.client import LLMClient
from app.logging_config import configure_logging, get_logger
from app.realtime.broker import ReviewBroker
from app.services.job_manager import JobManager

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "STUB (no API key)" if not settings.has_llm else f"model={settings.model}"
    logger.info("CodeSage %s starting up — %s", __version__, mode)
    # Real-time progress fan-out + background review runner, shared across requests.
    app.state.broker = ReviewBroker()
    app.state.job_manager = JobManager(app.state.broker)
    yield
    logger.info("CodeSage shutting down")


app = FastAPI(
    title="CodeSage",
    description="Agentic Code Review Assistant — multi-agent PR review on LangGraph.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "name": "CodeSage",
        "version": __version__,
        "llm_mode": "stub" if not settings.has_llm else "live",
        "model": settings.model,
        "docs": "/docs",
    }


def run() -> None:
    """Console-script entrypoint (`codesage-api`)."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


# Touch LLMClient at import so misconfiguration surfaces early in logs (cheap).
_ = LLMClient
