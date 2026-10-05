import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients import create_http_client, create_redis
from app.config import settings
from app.db import engine

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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


app = FastAPI(title="FastAPI RevenueCat", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
