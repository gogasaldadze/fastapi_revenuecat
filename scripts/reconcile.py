"""Report, and optionally fix, drift between the database and RevenueCat.

    python -m scripts.reconcile            report only
    python -m scripts.reconcile --fix      write RevenueCat's answer

Exits with status 1 when drift was found, so a scheduler can alert on it.
"""

import argparse
import asyncio
import json
import sys

from app.core.clients import create_http_client
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal, engine
from app.services.reconcile import reconcile


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--fix", action="store_true", help="write RevenueCat's answer to the DB")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=5)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    """Run one reconcile pass and print a JSON summary."""
    args = parse_args(argv)
    configure_logging(settings.log_level, settings.log_format)
    async with create_http_client() as http:
        report = await reconcile(
            SessionLocal,
            http,
            fix=args.fix,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
        )
    await engine.dispose()
    summary = {
        "checked": report.checked,
        "unknown": report.unknown,
        "drift": len(report.drift),
        "fixed": report.fixed,
    }
    print(json.dumps(summary))
    return 1 if report.drift else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
