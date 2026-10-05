# Security

What this template defends against, and how. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Threat model

Attackers can run the app on a jailbroken device, read and change every request it sends, call the API directly, and create as many accounts as they like. They do not have the server secrets.

### Client claims premium

*Attack:* send `X-Is-Premium: true`, or the RevenueCat id of someone who paid, with every request.

*Mitigation:* the server never reads premium state from a request. `require_premium` loads the user row from the token and asks RevenueCat about the id bound to that row. The only way to bind an id is `POST /subscription/sync`, which checks RevenueCat first.

### Forged webhooks

*Attack:* post an `INITIAL_PURCHASE` for your own id to `/webhooks/revenuecat`.

*Mitigation:* the Authorization header is compared with `REVENUECAT_WEBHOOK_SECRET` in constant time (`hmac.compare_digest`). A wrong header gets 401. When the secret is not configured and `DEBUG` is off, every webhook gets 503: a misconfigured server rejects events instead of trusting them. Even an accepted forged event cannot bind an id to a user, and premium checks for bound users ask RevenueCat directly.

### Sandbox purchases in production

*Attack:* make free sandbox purchases with a TestFlight or debug build pointed at production.

*Mitigation:* sandbox webhook events are ignored, and sandbox purchases count as INACTIVE in the REST check, unless `DEBUG` or `ALLOW_SANDBOX_SUBSCRIPTIONS` is on. One predicate (`settings.sandbox_allowed`) drives both paths.

### Taking over someone else's subscription

*Attack:* learn another user's RevenueCat id and call `/subscription/sync` with it. The id would move to the attacker.

*Mitigation:* the sync only binds ids RevenueCat reports as active, and every move is logged as a warning with both user ids. The remaining risk is someone who knows a paying user's id. Anonymous ids are random and hard to guess. The strongest setup is to call `Purchases.logIn` with your own user id and reject syncs where `app_user_id` does not equal the caller's id; this is listed on the roadmap as a setting.

### Quota abuse

*Attack:* hammer a costly endpoint, or make Redis unavailable to get around limits.

*Mitigation:* `charge()` counts weighted units per user per UTC day in Redis and returns 429 at the limit. Denied requests are refunded so they do not count. When Redis is down, only features listed in `FAIL_OPEN_FEATURES` run; costly ones return 503.

Quotas are per user, not per IP. Put a rate limit in front of `/auth` and `/subscription/sync` (at the proxy or with a library such as slowapi) so account creation and sync calls cannot be used to probe RevenueCat ids.

### Token forgery

*Mitigation:* tokens are HS256 JWTs signed with `JWT_SECRET`. `exp` and `sub` are required, the algorithm list is fixed, and the app does not start without a secret. `/auth/dev_token` returns 404 unless `DEBUG` is on.

### Information leaks

* Errors use one envelope with a short message. Stack traces only go to logs.
* Logs carry user ids and RevenueCat ids, not tokens or secrets.
* `subscription_events.payload` stores the raw webhook body. It can include prices and country codes. Set `STORE_WEBHOOK_PAYLOADS=false` if you do not want that.

## Checklist for production

* `DEBUG=false`
* `JWT_SECRET`, `REVENUECAT_SECRET_KEY` and `REVENUECAT_WEBHOOK_SECRET` set from a secret store, not committed
* The same webhook secret pasted into the RevenueCat dashboard
* `ALLOW_SANDBOX_SUBSCRIPTIONS=false`
* HTTPS only, at the load balancer or proxy
* A rate limit on auth and sync routes
* The reconcile job scheduled, with alerts on a nonzero exit
