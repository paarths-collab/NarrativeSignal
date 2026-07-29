"""Command-line entry point: `python -m enrich {categorize,label,all} --in --out`.

Mirrors the shape of `fetcher/cli.py`: `parse_args(argv)` and `run(argv)` are
kept separate so both are directly testable without spawning a subprocess.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from enrich.categorize_domains import _reseed_defaults, categorize
from enrich.label_topics import label_topics
from etl.categories import CategoryStore

LOG = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="enrich")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_io_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--in", dest="in_dir", required=True, type=Path,
            help="Input directory (e.g. domain_categories.json, uncategorized_domains.txt, "
            "narrative_topic_mapping.csv, narrative_intelligence_summary.csv).",
        )
        p.add_argument(
            "--out", dest="out_dir", required=True, type=Path,
            help="Output directory. Written files are created here.",
        )
        p.add_argument("-v", "--verbose", action="store_true")

    categorize_p = sub.add_parser(
        "categorize", help="categorize new domains against the fixed vocab"
    )
    _add_io_args(categorize_p)

    label_p = sub.add_parser(
        "label", help="replace LABEL_ME_* placeholders with real topic labels"
    )
    _add_io_args(label_p)

    all_p = sub.add_parser("all", help="run categorize then label")
    _add_io_args(all_p)

    return parser.parse_args(argv)


def _do_categorize(in_dir: Path, out_dir: Path) -> int:
    in_path = Path(in_dir) / "domain_categories.json"
    store = CategoryStore(in_path)
    _reseed_defaults(store)

    worklist_path = Path(in_dir) / "uncategorized_domains.txt"
    worklist: list[str] = []
    if worklist_path.exists():
        worklist = [
            line.strip()
            for line in worklist_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # Count domains already known (cache hits) before mutating anything, so
    # the printed summary distinguishes "already cached" from "capped/deferred".
    deduped = list(dict.fromkeys(worklist))
    cached = sum(1 for d in deduped if store.category_of(d) is not None)

    asked, skipped = categorize(store, worklist)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store.path = out_dir / "domain_categories.json"
    store.save()

    print(f"categorize: asked={asked} skipped={skipped} cached={cached}")
    return 0


def _do_label(in_dir: Path, out_dir: Path) -> int:
    in_dir = Path(in_dir)
    topic_map_df = pd.read_csv(in_dir / "narrative_topic_mapping.csv")
    summary_df = pd.read_csv(in_dir / "narrative_intelligence_summary.csv")

    result = label_topics(topic_map_df, summary_df)

    n_clusters = int(topic_map_df["topic_cluster"].nunique())
    still_placeholder = result["topic_label"].astype(str).str.startswith("LABEL_ME_")
    n_missed = int(result.loc[still_placeholder, "topic_cluster"].nunique())

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(
        out_dir / "narrative_topic_mapping.csv", index=False, lineterminator="\n"
    )

    print(f"label: labelled={n_clusters} clusters, N_missed={n_missed}")
    return 0


def _do_all(in_dir: Path, out_dir: Path) -> int:
    """Run categorize then label. A categorize failure is graceful: label
    still runs (it reads its own inputs from `in_dir`, independent of
    whatever categorize did or didn't manage to write)."""
    categorize_rc = 0
    try:
        _do_categorize(in_dir, out_dir)
    except Exception:
        LOG.exception("categorize step failed; continuing to label")
        categorize_rc = 1

    label_rc = _do_label(in_dir, out_dir)

    return label_rc if categorize_rc == 0 else categorize_rc


def run(argv: list[str]) -> int:
    ns = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if ns.verbose else logging.INFO)
    try:
        if ns.cmd == "categorize":
            return _do_categorize(ns.in_dir, ns.out_dir)
        if ns.cmd == "label":
            return _do_label(ns.in_dir, ns.out_dir)
        if ns.cmd == "all":
            return _do_all(ns.in_dir, ns.out_dir)
    except Exception:
        LOG.exception("enrich %s failed", ns.cmd)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
