# fastapi_revenuecat

[![ci](https://github.com/gogasaldadze/fastapi_revenuecat/actions/workflows/ci.yml/badge.svg)](https://github.com/gogasaldadze/fastapi_revenuecat/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A FastAPI template that keeps subscription state for apps that sell through RevenueCat. It takes RevenueCat webhooks, checks entitlements against the RevenueCat API, and gives your routes a `require_premium` dependency you can trust.

## Why

Subscription backends tend to fail in the same few ways. This template is built to avoid them:

* Trusting the client. A header like `X-Is-Premium` or a client sent RevenueCat id is never used to decide access.
* Treating CANCELLATION or BILLING_ISSUE as lost access. The user paid for the rest of the period.
* Counting sandbox purchases in production. They are free for anyone with a test build.
* Answering 200 to a webhook that failed. RevenueCat retries failed deliveries; a 200 throws the retry away and the event is lost.
* Answering 500 to a webhook that can never succeed. Malformed and irrelevant events get 200 so they are not retried for nothing.
* Ignoring expiry dates, or letting a late event move them backwards.

[docs/lessons_learned.md](docs/lessons_learned.md) has the story behind each one.

## Features

* `POST /webhooks/revenuecat` with constant time secret check, idempotent processing by event id, stale event protection and forward only expiry
* Entitlement check against the RevenueCat REST API with clear fail open and fail closed rules and a Redis cache
* `POST /subscription/sync` that binds a RevenueCat id to a user only after RevenueCat confirms it
* `require_premium` and `charge(feature)` dependencies, the second with weighted daily quotas in Redis
* A reconcile job that reports drift between your database and RevenueCat
* Postgres with async SQLAlchemy 2 and Alembic, Redis, JSON logs, one error format, `/health` and `/health/ready`
* 99 percent test coverage with no services needed to run the tests, strict mypy, ruff, Docker, CI

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

The API is on http://localhost:8000 and the OpenAPI docs on http://localhost:8000/docs. Compose runs with `DEBUG=true`, which enables a test token endpoint:

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/dev_token | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s localhost:8000/me -H "Authorization: Bearer $TOKEN"
# {"id":"...","is_premium":false}
```

Run the tests (Python 3.12, no Docker needed):

```bash
make install
make check   # ruff, mypy, pytest with coverage
```

## Connect RevenueCat

1. Set `REVENUECAT_SECRET_KEY` (Secret API key, `sk_...`) and `REVENUECAT_ENTITLEMENT_ID`.
2. Generate a webhook secret and set it as `REVENUECAT_WEBHOOK_SECRET`.
3. In the RevenueCat dashboard add a webhook to `https://your.domain/webhooks/revenuecat` with the Authorization header value `Bearer <your secret>`.
4. Send a test event from the dashboard. It should get a 200.

## Events

| Event | What the server does |
|---|---|
| INITIAL_PURCHASE, RENEWAL, PRODUCT_CHANGE, UNCANCELLATION | Premium on for bound users; expiry moves forward |
| EXPIRATION | Premium off |
| TRANSFER | Premium moves from users bound to the old ids to users bound to the new ones |
| CANCELLATION, BILLING_ISSUE, SUBSCRIPTION_PAUSED | Recorded only; access continues until the period ends |
| Sandbox events | Ignored unless sandbox is allowed |
| Anything else, including TEST | Recorded and logged |

Details, status codes and curl examples: [docs/webhooks.md](docs/webhooks.md).

## The iOS side

```swift
Purchases.configure(withAPIKey: revenueCatPublicKey)
_ = try await Purchases.shared.logIn(currentUser.id)

let result = try await Purchases.shared.purchase(package: package)
if !result.userCancelled { try await api.syncSubscription() }

_ = try await Purchases.shared.restorePurchases()
try await api.syncSubscription()   // always, after every restore
```

`syncSubscription()` posts `{"app_user_id": Purchases.shared.appUserID}` to `/subscription/sync`. The full version, with the delegate that syncs when an entitlement turns on, is in [docs/ios_integration.md](docs/ios_integration.md).

## Using it in your routes

```python
from fastapi import Depends

from app.api.deps import PremiumUser, charge, require_premium


@router.get("/reports")
async def reports(user: PremiumUser) -> ReportList: ...


@router.post(
    "/generate",
    dependencies=[Depends(require_premium), Depends(charge("heavy"))],
)
async def generate() -> Result: ...
```

Auth is a minimal JWT setup so the template runs on its own. Replace `get_current_user` in `app/core/security.py` with your own; it only has to return a `User` or raise 401.

## Documentation

* [Architecture](docs/architecture.md): components, flows with sequence diagrams, sources of truth
* [Webhooks](docs/webhooks.md): every event, status codes, idempotency, testing
* [Entitlements](docs/entitlements.md): status model, fail open and fail closed, caching
* [Security](docs/security.md): threat model and mitigations
* [Configuration](docs/configuration.md): every environment variable
* [Deployment](docs/deployment.md): Docker, Railway, migrations, scaling
* [iOS integration](docs/ios_integration.md): the app side
* [Production lessons](docs/lessons_learned.md): what went wrong before this existed

## Design decisions

* **RevenueCat decides, the database remembers.** Premium checks for bound users ask RevenueCat (cached for minutes). The stored state is used for users you upgrade by hand and during RevenueCat outages.
* **Webhooks move state, clients only ask.** A webhook is the only writer once it has touched a row. The sync endpoint verifies with RevenueCat and seeds rows no webhook has written yet.
* **Status codes are for retries.** 503 when trying again could help, 200 when it cannot.
* **No network calls under a row lock.** RevenueCat is called first, rows are locked after, and Postgres sessions set `lock_timeout`.
* **Expiry only moves forward.** Replays and late deliveries cannot shorten or extend access the wrong way.
* **v1 REST endpoint.** It needs only a secret key. The v2 API works too; see [docs/entitlements.md](docs/entitlements.md).

Why each of these exists: [docs/lessons_learned.md](docs/lessons_learned.md).

## Deploy on Railway

1. Create a project with PostgreSQL and Redis, then add this repository as a service.
2. Set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`, `REDIS_URL` to `${{Redis.REDIS_URL}}`, and the secrets from `.env.example`.
3. Set the command that runs before each deploy to `alembic upgrade head` and the health check path to `/health`.
4. Point the RevenueCat webhook at your Railway domain.

More in [docs/deployment.md](docs/deployment.md).

## Project layout

```
app/
  api/            routes, schemas, dependencies (require_premium, charge)
  core/           settings, auth, errors, logging, shared clients
  db/             models, session, row lock helpers
  services/       RevenueCat client, webhook processing, premium rules, quotas
  main.py
migrations/       Alembic
scripts/          reconcile job
tests/            pytest, runs on SQLite and fakeredis
docs/
```

## FAQ

**Do I need the webhook if premium checks call RevenueCat anyway?**
Yes. It keeps the stored state current for outages, invalidates the cache the moment something changes, records every event, and handles TRANSFER.

**Can I use my own user ids as RevenueCat app user ids?**
Yes, and you should. Call `Purchases.logIn` with your user id before showing a paywall. Anonymous ids also work.

**What happens if Redis goes down?**
Entitlement checks go straight to RevenueCat. Features in `FAIL_OPEN_FEATURES` keep working; other metered features return 503. Nothing becomes premium because of it.

**What happens if RevenueCat goes down?**
Users who were already premium, and whose stored expiry has not passed, keep access. Nobody new gets it. Webhooks queue on RevenueCat's side.

**Does it support Android?**
The server does not care which store a purchase came from. The Android SDK follows the same order as the iOS guide.

**Does it support several entitlements or tiers?**
Not yet. It answers "premium or not" for one entitlement. See the roadmap.

## Roadmap

* A setting that requires `app_user_id` to equal the caller's user id in `/subscription/sync`
* Several entitlements, with tiers derived from webhook product ids
* Handle `SUBSCRIPTION_EXTENDED`, and treat refunds (CANCELLATION with a refund reason) as immediate expiry
* Support for more than one RevenueCat project on one server
* A cleanup command for old `subscription_events` rows
* An optional v2 REST client

## License

MIT. See [LICENSE](LICENSE).
