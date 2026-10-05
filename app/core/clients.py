"""Shared Redis and HTTP clients, created once in the app lifespan."""

from typing import Annotated

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.config import settings


def create_redis() -> Redis:
    """Build the Redis client. It connects lazily on first use."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def create_http_client() -> httpx.AsyncClient:
    """Build the HTTP client used for RevenueCat requests."""
    return httpx.AsyncClient(timeout=settings.revenuecat_timeout_seconds)


def get_redis(request: Request) -> Redis:
    """Dependency returning the app wide Redis client."""
    redis: Redis = request.app.state.redis
    return redis


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Dependency returning the app wide HTTP client."""
    client: httpx.AsyncClient = request.app.state.http_client
    return client


RedisClient = Annotated[Redis, Depends(get_redis)]
HttpClient = Annotated[httpx.AsyncClient, Depends(get_http_client)]
