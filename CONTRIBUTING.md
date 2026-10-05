# Contributing

Thanks for taking the time. Bug reports, fixes and documentation improvements are all welcome.

## Before you start

For anything bigger than a small fix, open an issue first so we can agree on the approach. The template tries to stay small, and some features are better left to the people who use it.

## Setup

You need Python 3.12. Docker is only needed to run the full stack.

```bash
make install
make check
```

`make check` runs ruff, strict mypy and the tests with coverage. CI runs the same steps and fails under 90 percent coverage.

The tests use SQLite, fakeredis and respx, so they need no running services. No test may call the real RevenueCat API.

## Pull requests

* Keep each pull request to one change.
* Add or update tests for every behaviour change. Webhook changes need a test for each event type they touch.
* Update the docs when behaviour or configuration changes, and add an entry under Unreleased in `CHANGELOG.md`.
* Schema changes need an Alembic migration. Run `make revision m="..."` and read the file it creates.
* Commit messages: short imperative summary, details in the body if needed.

## Style

* Ruff handles formatting and import order. Run `make format`.
* Type hints on everything. mypy runs in strict mode.
* Services take their dependencies as arguments and do not import FastAPI.
* Comments explain why, not what.
