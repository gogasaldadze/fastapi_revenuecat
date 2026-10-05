# Entitlements

`app/services/entitlements.py` asks RevenueCat whether an app user id currently holds the entitlement named in `REVENUECAT_ENTITLEMENT_ID`.

## Why the v1 endpoint

The check uses `GET https://api.revenuecat.com/v1/subscribers/{app_user_id}`. It works with a secret key alone and returns entitlements with their expiry and grace period dates and the sandbox flag of each purchase.

The v2 API (`/v2/projects/{project_id}/customers/{customer_id}/...`) also works. It needs a project id and a v2 secret key with the right permissions, and its product ids are RevenueCat internal ids, not store ids. Swapping it in only touches `fetch_entitlement` and `read_entitlement`.

## Status model

| Status | Meaning | Comes from |
|---|---|---|
| `ACTIVE` | The entitlement grants access now | 200, entitlement present, expiry or grace period in the future, or no expiry (lifetime) |
| `INACTIVE` | Known customer, no access | 200 with the entitlement missing, expired, or from a sandbox purchase while sandbox is not allowed |
| `NOT_FOUND` | RevenueCat has never seen this id | 404 |
| `UNKNOWN` | No answer | 5xx after one retry, 401, 429, timeout, network error, unreadable body, missing secret key |

Expiry is always compared with the current time. A 200 that still lists an old entitlement is INACTIVE.

## Fail open or fail closed

| Status | Bound user, stored premium not expired | Bound user, otherwise | Why |
|---|---|---|---|
| `ACTIVE` | premium | premium | RevenueCat confirmed it |
| `INACTIVE` | not premium | not premium | RevenueCat confirmed it |
| `NOT_FOUND` | not premium | not premium | This id never paid. Granting access here would let any new account in. |
| `UNKNOWN` | premium | not premium | An outage should not lock out people who paid, but it must not grant anything new either |

Users without a bound id use the stored row alone, with its expiry enforced. That covers accounts you upgrade by hand.

During an outage the stored expiry still applies. A subscriber whose period ended during the outage loses access when the period ends, not when RevenueCat comes back.

## Caching

Results are cached in Redis under `rc_entitlement:{app_user_id}`.

* `ACTIVE`, `INACTIVE` and `NOT_FOUND` are cached for `ENTITLEMENT_CACHE_TTL_SECONDS`.
* An `ACTIVE` result is never cached past its own expiry, so the cache cannot extend access.
* `UNKNOWN` is never cached. The next request asks again.
* Cache read and write errors are logged and ignored. Redis being down means more REST calls, nothing else.

## Invalidation

* Every webhook event drops the cache for every id it mentions, whatever its type.
* `POST /subscription/sync` always skips the cache for the id it checks, because it runs right after a purchase or restore.
* The reconcile job reads RevenueCat directly and never uses the cache.

## Retries

A read is retried once after `REVENUECAT_RETRY_DELAY_SECONDS` on a network error or a 5xx. Client errors are not retried. The template makes no REST writes. If you add some (granting promotional entitlements, for example), give each one an idempotency key that stays the same across retries, and retry 423 and 429 with backoff.

## Tiers and product ids

The template only answers "premium or not". If you add tiers or billing periods, derive them from the store `product_id` in webhook events. Do not derive them from REST product ids, and do not trust a tier the client sends. A client value may seed a missing field and must never overwrite what a webhook wrote.
