"""End-to-end: fetcher -> ETL, offline, using the fake Reddit client.

Proves that Phase 2 wires together: `fetch_day` against the fake produces raw
records that `write_jsonl` serializes, `normalize_listing` parses, and
`run_pipeline` turns into the full set of 19 REQUIRED_ARTIFACTS on disk.

Note on the fake input: the shared `fake_reddit` fixture replays the SAME 20
canned records for every subreddit -- same ids, same authors. After
`fetch_day` tags each record with its source-sub, every title still maps to
a single author (breaks clustering) and every id repeats 10x (breaks
`set_index("id")`). The test's `_diversify` helper rewrites each cross-sub
copy's id and author to make the fake output shaped like real Reddit data
-- globally-unique post ids, and enough author variety per title to
produce non-empty clusters. That's the minimum massaging that lets the
pipeline exercise every stage; the fetcher's raw payload shape is
otherwise untouched, so this still proves the write_jsonl -> normalize
handoff works.
"""
import asyncio
from datetime import date
from pathlib import Path


def _diversify(records: list[dict]) -> list[dict]:
    """Make the fake-replayed records look like real Reddit data.

    Two fixes applied per (subreddit, id) pair:

    1. Each cross-sub copy gets a unique synthetic author. Same title across
       N subs -> N distinct authors, so `groupby("title")["author"].nunique()
       > 1` holds and clustering produces non-empty edges.

    2. Each cross-sub copy gets a subreddit-scoped id. Real Reddit post IDs
       are globally unique, so the fetcher intentionally does NOT dedupe
       cross-sub; the fake fixture replaying identical ids violates that
       assumption and breaks `assign_clusters`'s `set_index("id")`.

    Everything else in the record is preserved, so the fetcher's raw
    payload shape is untouched.
    """
    out = []
    for rec in records:
        copy = dict(rec)
        sub = rec["subreddit"]
        copy["author"] = f"u_{sub}_{rec['id']}"
        copy["id"] = f"{rec['id']}_{sub}"
        out.append(copy)
    return out


def test_fetcher_output_drives_the_full_pipeline(tmp_path, fake_reddit):
    from fetcher.fetch import fetch_day
    from fetcher.serialize import write_jsonl
    from etl.normalize import normalize_listing
    from etl.pipeline import REQUIRED_ARTIFACTS, run_pipeline

    # Step 1: fetch, using the fake -- 20 canned records replayed per subreddit.
    # The canned page's records live in a Feb-17 2025 UTC window, so the
    # UTC-day filter keeps them.
    raw = asyncio.run(fetch_day(fake_reddit, day=date(2025, 2, 17)))
    assert raw, "the fake must yield at least one Feb-17 record"

    # Ensure clusterable diversity (see module docstring).
    raw = _diversify(raw)

    jsonl = tmp_path / "day.jsonl"
    write_jsonl(raw, jsonl)

    # Step 2: normalize
    clean_posts = normalize_listing(jsonl)
    assert set(clean_posts.columns) == {
        "id", "created_datetime", "subreddit", "author", "title", "domain",
        "score", "num_comments", "permalink", "url",
    }
    assert len(clean_posts) > 0, (
        "normalize dropped every row -- the fetcher/serialize contract is broken"
    )

    # Step 3: full pipeline. `categories_path` is resolved from the repo root,
    # not tmp_path, since the seed allowlist ships with the repo.
    repo_root = Path(__file__).resolve().parents[2]
    out = tmp_path / "artifacts"
    run_pipeline(
        clean_posts,
        out,
        categories_path=repo_root / "data" / "domain_categories.json",
    )

    # Every REQUIRED_ARTIFACTS entry must exist and be non-empty. An empty
    # file would satisfy `.exists()` but silently break every downstream
    # consumer that reads it, so check byte size too.
    missing = [n for n in REQUIRED_ARTIFACTS if not (out / n).exists()]
    assert missing == [], f"pipeline did not produce: {missing}"

    empty = [n for n in REQUIRED_ARTIFACTS if (out / n).stat().st_size == 0]
    assert empty == [], f"pipeline produced empty artifacts: {empty}"


def test_patch_db_tables_are_all_present(tmp_path, fake_reddit):
    """The backend's `patch_db.TABLES` contract must be satisfied by the pipeline
    output -- every CSV the backend expects to load must exist on disk after
    a fetch -> normalize -> run_pipeline round trip.
    """
    import sys

    from fetcher.fetch import fetch_day
    from fetcher.serialize import write_jsonl
    from etl.normalize import normalize_listing
    from etl.pipeline import run_pipeline

    repo_root = Path(__file__).resolve().parents[2]
    # `patch_db.TABLES` lives at `backend/patch_db.py`; the backend dir is
    # not on sys.path by default.
    sys.path.insert(0, str(repo_root / "backend"))
    try:
        from patch_db import TABLES  # noqa: E402
    finally:
        sys.path.pop(0)

    raw = asyncio.run(fetch_day(fake_reddit, day=date(2025, 2, 17)))
    raw = _diversify(raw)
    jsonl = tmp_path / "day.jsonl"
    write_jsonl(raw, jsonl)
    clean_posts = normalize_listing(jsonl)

    out = tmp_path / "artifacts"
    run_pipeline(
        clean_posts,
        out,
        categories_path=repo_root / "data" / "domain_categories.json",
    )

    # TABLES is {logical_name: csv_filename}; every referenced CSV must live
    # in the output directory.
    referenced = set(TABLES.values())
    missing = [n for n in sorted(referenced) if not (out / n).exists()]
    assert missing == [], (
        f"patch_db.TABLES references CSVs the pipeline did not produce: {missing}"
    )
