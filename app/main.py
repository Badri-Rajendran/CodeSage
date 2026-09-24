"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import public_router, router
from app.config import get_settings
from app.db.session import SessionFactory
from app.llm.client import LLMClient
from app.logging_config import configure_logging, get_logger
from app.realtime.broker import ReviewBroker
from app.services.job_manager import JobManager
from app.services.review_service import ReviewService

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mode = "STUB (no API key)" if not settings.has_llm else f"model={settings.model}"
    logger.info("CodeSage %s starting up — %s", __version__, mode)
    if settings.auth_disabled:
        logger.warning("API authentication is DISABLED (CODESAGE_AUTH_DISABLED); local dev only.")
    elif not settings.api_key_list:
        logger.error("No CODESAGE_API_KEYS configured — all /api/v1 routes will return 503.")
    if settings.sandbox_enabled:
        logger.warning(
            "Sandboxed test execution is ENABLED: diff-supplied tests run with process-level "
            "isolation only. Use only with trusted input."
        )
    async with AsyncExitStack() as stack:
        checkpointer = await _open_checkpointer(stack)
        await _reconcile_interrupted()
        # Real-time progress fan-out + background review runner, shared across requests.
        app.state.broker = ReviewBroker()
        app.state.job_manager = JobManager(app.state.broker, checkpointer)
        yield
        logger.info("CodeSage shutting down")


async def _open_checkpointer(stack: AsyncExitStack):
    """LangGraph's Postgres checkpointer: lets a review pause at the gate and resume
    after a restart. Without it reviews still run, but the gate can't pause."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn)
        )
        await saver.setup()
        logger.info("Checkpointer ready: the approval gate pauses reviews.")
        return saver
    except Exception as exc:  # noqa: BLE001 — degrade, don't refuse to start
        logger.error("Checkpointer unavailable (%s); the approval gate will not pause.", exc)
        return None


async def _reconcile_interrupted() -> None:
    try:
        async with SessionFactory() as session:
            n = await ReviewService(session).reconcile_interrupted()
        if n:
            logger.warning("Marked %d review(s) interrupted by the restart as failed.", n)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not reconcile interrupted reviews (%s).", exc)


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

app.include_router(public_router)
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
