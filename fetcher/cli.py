"""Command-line entry point: `python -m fetcher fetch --date --out`."""
import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fetcher.client import make_reddit_client
from fetcher.fetch import fetch_day
from fetcher.serialize import write_jsonl


def _yesterday_utc() -> str:
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fetcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch", help="fetch one UTC day into a JSONL file")
    fetch.add_argument(
        "--date", default=_yesterday_utc(),
        help="UTC calendar day (YYYY-MM-DD). Default: yesterday.",
    )
    fetch.add_argument(
        "--out", required=True, type=Path,
        help="Output JSONL path. Written atomically.",
    )
    fetch.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


async def _do_fetch(day: date, out: Path) -> int:
    reddit = make_reddit_client()
    try:
        records = await fetch_day(reddit, day=day)
    finally:
        # Real asyncpraw.Reddit exposes an async close(); test doubles (e.g. the
        # fake_reddit fixture) don't need one. Close only when present so the CLI
        # stays usable against either.
        close = getattr(reddit, "close", None)
        if close is not None:
            await close()
    write_jsonl(records, out)
    print(f"wrote {len(records)} records to {out}")
    return 0


def run(argv: list[str]) -> int:
    ns = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if ns.verbose else logging.INFO)
    if ns.cmd == "fetch":
        return asyncio.run(_do_fetch(date.fromisoformat(ns.date), ns.out))
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
