# Production lessons

We have run RevenueCat subscriptions behind a FastAPI backend in production. This template exists because of the mistakes below. Each one cost us money, support time or a paying user's trust.

### CANCELLATION and BILLING_ISSUE revoked access

*What went wrong:* users lost what they had paid for the moment they turned off auto renew, or the moment a card was declined.

*Why:* both events were in the same set as EXPIRATION. CANCELLATION means the subscription will not renew; BILLING_ISSUE starts a grace period. Neither ends access.

*What the template does:* both are info only, along with SUBSCRIPTION_PAUSED, which RevenueCat sends when a pause is scheduled for the end of the period. Only EXPIRATION removes access. A test covers every event type.

### A 404 from RevenueCat granted premium

*What went wrong:* our sync endpoint treated any failed verification as "trust the client". A 404 counted as a failure, so freshly created accounts that had never paid granted themselves premium. People found it and used it.

*Why:* 404 is not an outage. It is RevenueCat telling you this customer does not exist.

*What the template does:* NOT_FOUND fails closed. Only timeouts and 5xx fall back to stored state, and only for users already premium and not past their stored expiry.

### The webhook answered 200 to everything, and a purchase was lost

*What went wrong:* during a database write stall, an INITIAL_PURCHASE failed halfway. The handler logged the error and answered 200. RevenueCat considered the event delivered and never sent it again.

*Why:* we believed RevenueCat does not retry. It does retry deliveries that fail.

*What the template does:* temporary failures answer 503 so RevenueCat delivers again. Events that can never succeed (malformed, unknown, sandbox, unbound id) get 200. A unique event id makes the retries safe.

### Renewals did not move the period end

*What went wrong:* the stored period end stayed where the first purchase put it. Expiry logic and usage counters ran on a frozen date.

*Why:* the period was computed locally from the plan length instead of read from the event.

*What the template does:* the expiry comes from `expiration_at_ms`, only ever moves forward, and events older than the last one applied to a row are skipped.

### TRANSFER events were dropped

*What went wrong:* when RevenueCat moved a subscription to a new app user id, our handler ignored it. Old and new accounts both looked wrong, and a new account could claim perks meant to be granted once.

*Why:* TRANSFER has no `app_user_id`. It carries `transferred_from` and `transferred_to`, and our "missing app_user_id" check threw it away.

*What the template does:* TRANSFER has its own path that moves premium between the users bound to those ids.

### Anonymous and unexpected ids crashed the webhook

*What went wrong:* events for `$RCAnonymousID:` ids, and ids from another auth system, reached a query on a UUID column and failed.

*Why:* RevenueCat sends whatever id the app used at purchase time.

*What the template does:* RevenueCat ids are stored as strings, separate from user ids. Events only match ids that a user has bound through a verified sync. Unbound ids are skipped with a log line.

### The billing period was read from the wrong product id

*What went wrong:* annual and weekly subscribers were stored as monthly.

*Why:* the code derived the period from the product id in a REST response. That id is internal to RevenueCat and matched none of our rules, so everything fell through to the default. Another path trusted a period the client sent.

*What the template does:* REST only answers "active or not". The client cannot set any subscription field. If you add tiers, derive them from the store product id in webhook events.

### Purchases landed on anonymous ids

*What went wrong:* a purchase made before `Purchases.logIn` attached to the device's anonymous RevenueCat id, and the backend could not connect it to the user.

*Why:* the paywall could open before the login call had finished.

*What the template does:* the sync binds whatever id the SDK reports, once RevenueCat confirms it is active. The iOS guide configures the SDK first and awaits `logIn` before any paywall.

### Sync and webhook raced on the first write

*What went wrong:* a client sync and the purchase webhook arrived together during a slow period on the database. One got a unique violation, the sync returned 500, and the webhook lost its whole transaction.

*Why:* nothing decided who wins, and one failed insert aborted everything around it.

*What the template does:* the webhook owns a row once it has written to it. The sync only seeds rows no webhook has touched, writes inside a savepoint, and on a conflict reads the row again and reports it. Lock timeouts become 503, never 500.

### A network call inside a row lock hung requests

*What went wrong:* a RevenueCat call and an insert made while holding `FOR UPDATE` on a user row deadlocked against another connection. Requests hung for two minutes.

*Why:* the lock was held across work that waited on something else.

*What the template does:* RevenueCat is called before any lock is taken, and every Postgres transaction that locks subscription rows sets `lock_timeout`.

### Two code paths disagreed about sandbox

*What went wrong:* the REST check honoured the "allow sandbox" setting and the webhook did not. Staging behaved differently depending on which path ran first.

*Why:* the rule was written twice.

*What the template does:* one module talks to RevenueCat, and one setting (`settings.sandbox_allowed`) drives the webhook and the REST check.

### The webhook was never configured, and nobody noticed

*What went wrong:* the database showed a fraction of the subscribers RevenueCat showed.

*Why:* the URL and Authorization header had not been set in the RevenueCat dashboard, and nothing compared the two sides.

*What the template does:* `scripts/reconcile.py` checks every bound user against RevenueCat, reports drift, and exits nonzero so a scheduler can alert. `--fix` repairs rows with the same rules the webhook uses.

### A unique index blocked resubscribing

*What went wrong:* users who cancelled and came back got a 500 on every attempt.

*Why:* a unique index also covered rows that had been soft deleted.

*What the template does:* the RevenueCat id index is partial (`WHERE rc_app_user_id IS NOT NULL`). If you add soft deletes, add that condition to the index too.

### A locked customer lost a write

*What went wrong:* a write to RevenueCat got 423 Locked because another write to the same customer was in flight. The code treated it as fatal and the change was lost.

*Why:* 423 and 429 are temporary.

*What the template does:* it makes no REST writes. [entitlements.md](entitlements.md) explains what to do if you add some: a deterministic idempotency key reused on every retry, and backoff on 423 and 429.
