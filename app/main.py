"""Application entry point: `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import auth, examples, health, subscription, webhooks
from app.core.clients import create_http_client, create_redis
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared clients on startup and close them on shutdown."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is not set")
    app.state.http_client = create_http_client()
    app.state.redis = create_redis()
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.redis.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    configure_logging(settings.log_level, settings.log_format)
    application = FastAPI(
        title="FastAPI RevenueCat",
        version="0.1.0",
        description="Server side subscription state for apps that sell through RevenueCat.",
        lifespan=lifespan,
    )
    install_error_handlers(application)
    for router in (health.router, auth.router, subscription.router, webhooks.router):
        application.include_router(router)
    application.include_router(examples.router)
    return application


app = create_app()
