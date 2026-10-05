# Webhooks

Endpoint: `POST /webhooks/revenuecat`

## Setting it up

1. Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Set it as `REVENUECAT_WEBHOOK_SECRET` on the server.
3. In the RevenueCat dashboard open Project settings, Integrations, Webhooks, and add a webhook:
   * URL: `https://your.domain/webhooks/revenuecat`
   * Authorization header value: `Bearer <secret>`. The bare secret works too.
   * Environment: both. The server drops sandbox events itself unless sandbox is allowed.
4. Send a test event from the same page. You should see a 200 and a row with `event_type = TEST` in `subscription_events`.

The second step is the one people forget: the server can only check a header that RevenueCat has been told to send.

## Responses

The status code tells RevenueCat whether to try again. RevenueCat retries deliveries that do not get a 2xx a limited number of times with increasing delays; see [their webhook docs](https://www.revenuecat.com/docs/integrations/webhooks) for the current schedule.

| Situation | Status | Body |
|---|---|---|
| Wrong or missing Authorization | 401 | error envelope, `invalid_webhook_authorization` |
| `REVENUECAT_WEBHOOK_SECRET` empty and `DEBUG` off | 503 | error envelope, `webhook_not_configured` |
| Processed | 200 | `{"status": "ok", "bucket": "sync"}` |
| Same event id seen before | 200 | `{"status": "duplicate", "bucket": "..."}` |
| Body is not JSON, no event object, no `id` or `type` | 200 | `{"status": "ignored"}` |
| Unknown type, sandbox in production, id nobody has bound | 200 | `{"status": "ok", "bucket": "..."}` |
| Database error, lock timeout, anything unexpected | 503 | error envelope, `webhook_retry` |

The rule: answer 200 when a retry could not change the outcome, and 503 when it could. An early version of this code answered 200 to everything, and a purchase event was lost when the database had a short outage. See [lessons_learned.md](lessons_learned.md).

## Events

| Event | Bucket | What the server does |
|---|---|---|
| `INITIAL_PURCHASE` | sync | Bound users become premium. Expiry moves forward to `expiration_at_ms`, or becomes lifetime when it is null. |
| `RENEWAL` | sync | Same as above. |
| `PRODUCT_CHANGE` | sync | Same as above. The expiry still only moves forward. |
| `UNCANCELLATION` | sync | Same as above. |
| `EXPIRATION` | expire | Bound users lose premium. The stored expiry is kept. |
| `TRANSFER` | transfer | Users bound to an id in `transferred_from` lose premium. Users bound to an id in `transferred_to` get it, with the best expiry of the source users. No binding is created. |
| `CANCELLATION` | info_only | Recorded. Access continues until the period ends. Refunds also arrive as this event; the cache is dropped so the next check asks RevenueCat. |
| `BILLING_ISSUE` | info_only | Recorded. Access continues through the store grace period. |
| `SUBSCRIPTION_PAUSED` | info_only | Recorded. RevenueCat sends it when a pause is scheduled for the end of the period. EXPIRATION follows when access ends. |
| `TEST` and everything else | unknown | Recorded and logged. |

Every event, whatever the bucket, drops the cached entitlement check for every id it mentions.

## Matching users

An event can carry several ids for one customer: `app_user_id`, `original_app_user_id` and `aliases`. The server matches all of them against `users.rc_app_user_id`. It never creates a binding. A purchase made under an anonymous id that no user has synced yet is recorded and skipped; the sync after the purchase binds it.

## Idempotency and ordering

RevenueCat delivers at least once, and deliveries can arrive out of order. Three things keep that safe:

1. **Unique event id.** The event row is inserted before anything else. A second delivery hits the unique constraint and returns `duplicate` without touching users.
2. **Stale events are skipped.** Each user row remembers `rc_last_event_at`, the `event_timestamp_ms` of the newest event applied to it. An older event is ignored for that row.
3. **Expiry only moves forward.** A grant sets `premium_expires_at` to the later of the stored and the new value. A replay or a late RENEWAL cannot shorten access, and a lifetime purchase is never replaced with a date.

## Sandbox

Events with `environment = SANDBOX` are ignored unless `DEBUG` or `ALLOW_SANDBOX_SUBSCRIPTIONS` is on. Sandbox purchases cost nothing, and anyone with a TestFlight build can make them. The REST check applies the same rule to `is_sandbox` on the purchase.

## Testing

From the dashboard: use the send test event button on the webhook page. It must answer 200.

With curl against a local server:

```bash
curl -i http://localhost:8000/webhooks/revenuecat \
  -H "Authorization: Bearer $REVENUECAT_WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "api_version": "1.0",
    "event": {
      "id": "local_test_1",
      "type": "INITIAL_PURCHASE",
      "app_user_id": "user_123",
      "original_app_user_id": "user_123",
      "aliases": ["user_123"],
      "product_id": "monthly",
      "environment": "PRODUCTION",
      "event_timestamp_ms": 1790000000000,
      "expiration_at_ms": 1893456000000
    }
  }'
```

Send the same body again and the reply is `duplicate`. Change the id to send a new event.

## Retention

`subscription_events` grows by one row per event. Old rows are only needed to recognise repeated deliveries, which arrive within hours. Deleting rows older than a few months is safe; keep them longer if you use them for revenue reporting.
