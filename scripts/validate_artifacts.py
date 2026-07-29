"""Refuse to publish broken artifacts. Runs after the ETL, before the orphan push."""
import argparse
import json
import sys
from pathlib import Path


# The globe pipeline (not run_pipeline) produces this file and copies it in
# alongside the ETL's CSVs on data-live (see docs/superpowers/plans/
# 2026-07-29-globe-pipeline.md, Phase 5 Task 4). Its workflow step runs with
# continue-on-error: true, so this validator is the only signal that the globe
# run -- or the copy step -- actually succeeded.
GLOBE_ARTIFACT = "global_events_latest.json"

REQUIRED = [
    "clean_posts.csv", "clean_with_clusters_v2.csv", "graph_edge_intelligence_table.csv",
    "narrative_intelligence_summary.csv", "narrative_spread_chain_table.csv",
    "narrative_topic_mapping.csv", "narrative_registry.csv",
    "narrative_diffusion_table.csv", "amplification_event_table.csv",
    "author_amplification_summary.csv", "author_influence_profile.csv",
    "bridge_authors_v2.csv", "user_intelligence_summary.csv",
    "subreddit_intelligence_summary.csv", "subreddit_domain_flow_v2.csv",
    "clean_top_distinctive_domains.csv", "echo_chamber_scores.csv",
    "daily_volume_v2.csv", "ideological_distance_matrix.csv",
    GLOBE_ARTIFACT,
]


class ValidationError(RuntimeError):
    pass


def _count_rows(path: Path) -> int:
    """Data rows only (excludes header). Assumes UTF-8, standard CSV."""
    with path.open("r", encoding="utf-8") as fh:
        lines = sum(1 for _ in fh)
    return max(0, lines - 1)


def _validate_globe(path: Path) -> None:
    """Check `global_events_latest.json`: parseable JSON, and either an explicit
    empty-with-reason marker (P5 T1's no-data path) or at least one real event.

    An empty `events` list with no `reason` is treated as broken rather than
    benign: it means something failed silently rather than legitimately having
    nothing to report.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValidationError(f"{GLOBE_ARTIFACT}: unreadable ({e})") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValidationError(f"{GLOBE_ARTIFACT}: not valid JSON ({e})") from e

    if not isinstance(data, dict) or "events" not in data:
        raise ValidationError(f"{GLOBE_ARTIFACT}: missing 'events' key")

    events = data["events"]
    if not isinstance(events, list):
        raise ValidationError(f"{GLOBE_ARTIFACT}: 'events' is not a list")

    if len(events) == 0:
        reason = data.get("reason")
        if not reason or not str(reason).strip():
            raise ValidationError(
                f"{GLOBE_ARTIFACT}: events list is empty and no 'reason' was given"
            )


def validate(directory: Path) -> None:
    directory = Path(directory)
    missing = [n for n in REQUIRED if not (directory / n).exists()]
    if missing:
        raise ValidationError(f"missing: {', '.join(missing)}")

    for name in REQUIRED:
        if name == GLOBE_ARTIFACT:
            continue  # JSON, not CSV -- validated separately below
        path = directory / name
        if path.stat().st_size == 0:
            raise ValidationError(f"{name}: empty file")
        if _count_rows(path) == 0:
            raise ValidationError(f"{name}: no rows (header only)")

    _validate_globe(directory / GLOBE_ARTIFACT)

    stamp = directory / "generated_at.txt"
    if not stamp.exists() or not stamp.read_text().strip():
        raise ValidationError("generated_at.txt missing or empty")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="validate_artifacts")
    p.add_argument("directory", type=Path)
    args = p.parse_args(argv)
    try:
        validate(args.directory)
    except ValidationError as e:
        print(f"[validate] FAIL: {e}", file=sys.stderr)
        return 1
    print(f"[validate] OK: {len(REQUIRED)} artifacts present, non-empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
