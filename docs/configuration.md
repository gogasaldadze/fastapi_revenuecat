# Configuration

All settings come from environment variables. A `.env` file in the working directory is read too, and real environment variables win over it. Names are case insensitive.

`.env.example` lists every variable with a sensible starting value.

## Core

| Name | Default | Required | Description |
|---|---|---|---|
| `DEBUG` | `false` | no | Enables `/auth/dev_token`, skips webhook auth when no secret is set, and counts sandbox purchases. Never enable in production. |
| `LOG_LEVEL` | `INFO` | no | Root log level. |
| `LOG_FORMAT` | `json` | no | `json` for one JSON object per line, `text` for local work. |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/app` | yes in production | Postgres URL. Plain `postgres://` and `postgresql://` URLs are rewritten to use asyncpg. |
| `REDIS_URL` | `redis://localhost:6379/0` | yes in production | Used for the entitlement cache and daily quotas. |
| `LOCK_TIMEOUT_MS` | `3000` | no | How long a request waits for a row lock on Postgres before giving up with 503. |

## Auth

| Name | Default | Required | Description |
|---|---|---|---|
| `JWT_SECRET` | empty | yes | Signing key for bearer tokens. The app refuses to start without it. Use at least 32 random bytes. |
| `JWT_ALGORITHM` | `HS256` | no | Any HMAC algorithm PyJWT supports. |
| `JWT_TTL_SECONDS` | `2592000` | no | Token lifetime. 30 days by default. |

## RevenueCat

| Name | Default | Required | Description |
|---|---|---|---|
| `REVENUECAT_SECRET_KEY` | empty | yes | Secret API key (`sk_...`) of the same project the app uses. Without it every check returns UNKNOWN. |
| `REVENUECAT_WEBHOOK_SECRET` | empty | yes in production | The value RevenueCat sends in the Authorization header. Webhooks get 503 when it is empty and `DEBUG` is off. |
| `REVENUECAT_ENTITLEMENT_ID` | `premium` | yes | Entitlement identifier exactly as in the dashboard. Case matters. |
| `REVENUECAT_API_URL` | `https://api.revenuecat.com/v1` | no | Override for tests or a proxy. |
| `REVENUECAT_TIMEOUT_SECONDS` | `10` | no | Timeout per REST request. |
| `REVENUECAT_RETRY_DELAY_SECONDS` | `0.5` | no | Pause before the single retry of a failed REST read. |
| `ALLOW_SANDBOX_SUBSCRIPTIONS` | `false` | no | Count sandbox purchases outside debug. Useful for a staging server that TestFlight builds talk to. |
| `ENTITLEMENT_CACHE_TTL_SECONDS` | `300` | no | How long a REST result is cached. Active results are never cached past their expiry. |
| `STORE_WEBHOOK_PAYLOADS` | `true` | no | Keep the raw JSON of each webhook in `subscription_events.payload`. The row itself is always stored because it makes processing idempotent. |

## Quotas

| Name | Default | Required | Description |
|---|---|---|---|
| `FREE_DAILY_UNITS` | `20` | no | Units a free user may spend per UTC day. |
| `PREMIUM_DAILY_UNITS` | `1000` | no | Units a premium user may spend per UTC day. |
| `FEATURE_WEIGHTS` | `{"basic": 1, "heavy": 10}` | no | JSON object mapping feature name to units per request. Every name passed to `charge()` must be here, or the app fails at import. |
| `FAIL_OPEN_FEATURES` | `["basic"]` | no | JSON list of features that are allowed when Redis is down. Everything else gets 503. |

## Notes

* List and object values must be valid JSON, for example `FAIL_OPEN_FEATURES=["basic","search"]`.
* The settings object is created once at import. Restart the process after changing a variable.
