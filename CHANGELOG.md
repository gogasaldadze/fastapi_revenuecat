# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-05

### Added

* RevenueCat webhook endpoint with constant time secret check, event buckets, idempotent processing by event id, stale event protection, forward only expiry and TRANSFER handling.
* 503 for temporary webhook failures so RevenueCat retries, 200 for events that can never succeed.
* Entitlement check against the RevenueCat v1 REST API with ACTIVE, INACTIVE, NOT_FOUND and UNKNOWN states, a Redis cache and one retry on server errors.
* `POST /subscription/sync` that binds a RevenueCat id only after RevenueCat confirms it, yields to webhook state and handles concurrent binds.
* `require_premium` and `charge(feature)` dependencies with weighted daily quotas.
* Reconcile job that reports and optionally fixes drift between the database and RevenueCat.
* Minimal JWT auth with a debug only token endpoint.
* Alembic migration, Docker image, Docker Compose setup and CI with ruff, strict mypy and coverage.
* Documentation for architecture, webhooks, entitlements, security, configuration, deployment and the iOS side.

[Unreleased]: https://github.com/gogasaldadze/fastapi_revenuecat/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gogasaldadze/fastapi_revenuecat/releases/tag/v0.1.0
