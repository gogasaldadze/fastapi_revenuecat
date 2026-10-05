# Deployment

The app is one stateless container plus Postgres and Redis.

## Docker

```bash
docker build -t fastapi_revenuecat .
docker run --env-file .env -p 8000:8000 fastapi_revenuecat
```

The image is based on `python:3.12-slim`, runs as an unprivileged user, and listens on `$PORT` (8000 by default). It has a `HEALTHCHECK` against `/health`.

`docker compose up --build` starts Postgres, Redis, a one shot `migrate` service and the API, in that order.

## Migrations

Run migrations once per deploy, before the new version takes traffic. Do not run them in the container start command: with several replicas they would all race to migrate.

```bash
alembic upgrade head
```

Creating a migration after changing a model:

```bash
alembic revision --autogenerate -m "add column"
```

Read the new file before committing it. Autogenerate misses some changes, such as renamed columns and changes to partial indexes.

## Railway

1. Create a project and add the PostgreSQL and Redis services.
2. Add a service from your GitHub repository. Railway finds the Dockerfile.
3. Set variables on that service:
   * `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
   * `REDIS_URL` = `${{Redis.REDIS_URL}}`
   * `JWT_SECRET`, `REVENUECAT_SECRET_KEY`, `REVENUECAT_WEBHOOK_SECRET`, `REVENUECAT_ENTITLEMENT_ID`
4. In the service settings, set the command that runs before each deploy to `alembic upgrade head`.
5. Set the health check path to `/health`.
6. Generate a domain and point the RevenueCat webhook at `https://<domain>/webhooks/revenuecat`.

Railway hands out `postgresql://` URLs. The settings rewrite them to `postgresql+asyncpg://`, so the reference variable works as is.

For the reconcile job, add a second service from the same repository with the start command `python -m scripts.reconcile` and a cron schedule, for example `0 4 * * *`.

## Health checks

* `GET /health` answers 200 while the process runs. Use it for liveness and for platform health checks, so a Redis blip does not restart the app.
* `GET /health/ready` checks the database and Redis and answers 503 if either fails. Use it for readiness, so a load balancer stops routing to an instance that cannot serve.

## Scaling

* **Replicas.** The API keeps no state in memory. Run as many replicas as you need behind a load balancer.
* **Redis.** All replicas must share one Redis, or quotas are counted per replica and cached checks are not invalidated across them. A small managed instance is enough; it holds a few short lived keys per active user.
* **Database connections.** Each replica opens a SQLAlchemy pool (5 connections plus 10 overflow by default). Replicas times pool size must stay under the Postgres connection limit. Put PgBouncer in front if you go beyond that.
* **Webhooks.** Any replica can take any event. The unique event id and row locks make concurrent deliveries safe.
* **Lock timeout.** `LOCK_TIMEOUT_MS` bounds how long a request waits on a row lock. Webhooks that time out get 503 and are retried by RevenueCat.
* **Workers.** Prefer more containers over more uvicorn workers per container, so the platform can restart them independently.

## Reconcile job

```bash
python -m scripts.reconcile        # report drift
python -m scripts.reconcile --fix  # write RevenueCat's answer to drifted rows
```

It prints a JSON summary and exits with 1 when it found drift. Run the report daily and alert on failures. Drift usually means webhooks are not arriving: check the secret and the URL in the dashboard first.
