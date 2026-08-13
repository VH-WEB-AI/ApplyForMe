"""
ApplyForMe API entrypoint.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.shared_services.cache_service import cache_service

settings = get_settings()
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app_startup", env=settings.ENV)
    try:
        await cache_service.ping()
        logger.info("redis_connected")
    except Exception as exc:
        logger.error("redis_connection_failed", error=str(exc))
    yield
    logger.info("app_shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered career platform: resume intelligence, job matching, "
    "career health analytics, and an AI Career Copilot.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# Prometheus metrics at /metrics (Section 11: Observability & Ops)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


@app.get("/health", tags=["system"])
async def health_check():
    redis_ok = True
    try:
        await cache_service.ping()
    except Exception:
        redis_ok = False
    return {"status": "ok", "redis": redis_ok}
