"""Shared Redis and HTTP clients, created once in the app lifespan."""

from typing import Annotated

import httpx
from fastapi import Depends, Request
from redis.asyncio import Redis

from app.config import settings


def create_redis() -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


def create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.revenuecat_timeout_seconds)


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


RedisClient = Annotated[Redis, Depends(get_redis)]
HttpClient = Annotated[httpx.AsyncClient, Depends(get_http_client)]
