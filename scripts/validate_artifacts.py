"""Refuse to publish broken artifacts. Runs after the ETL, before the orphan push."""
import argparse
import sys
from pathlib import Path


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
]


class ValidationError(RuntimeError):
    pass


def _count_rows(path: Path) -> int:
    """Data rows only (excludes header). Assumes UTF-8, standard CSV."""
    with path.open("r", encoding="utf-8") as fh:
        lines = sum(1 for _ in fh)
    return max(0, lines - 1)


def validate(directory: Path) -> None:
    directory = Path(directory)
    missing = [n for n in REQUIRED if not (directory / n).exists()]
    if missing:
        raise ValidationError(f"missing: {', '.join(missing)}")

    for name in REQUIRED:
        path = directory / name
        if path.stat().st_size == 0:
            raise ValidationError(f"{name}: empty file")
        if _count_rows(path) == 0:
            raise ValidationError(f"{name}: no rows (header only)")

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
