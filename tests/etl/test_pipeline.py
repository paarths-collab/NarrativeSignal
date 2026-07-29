# tests/etl/test_pipeline.py
"""Contract tests for the orchestrator (spec §7).

The pipeline is run ONCE per session (it does real work over 8,687 rows) and every
test reads that one result. `pipeline_run` writes into a `tmp_path_factory`
directory — never into `data/`.
"""
import ast
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

from etl.config import DATA_DIR
from etl.pipeline import REQUIRED_ARTIFACTS, UNCATEGORIZED_DOMAINS_FILE, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))


@pytest.fixture(scope="session")
def pipeline_run(tmp_path_factory, golden_clean_posts):
    """(frames, out_dir) for a single full run against the golden 8,687 set."""
    out_dir = tmp_path_factory.mktemp("etl_artifacts")
    frames = run_pipeline(
        golden_clean_posts, out_dir, DATA_DIR / "domain_categories.json"
    )
    return frames, out_dir


@pytest.fixture(scope="session")
def frames(pipeline_run):
    return pipeline_run[0]


@pytest.fixture(scope="session")
def out_dir(pipeline_run) -> Path:
    return pipeline_run[1]


# ── The artifact list itself ─────────────────────────────────────────────────

def test_required_artifacts_is_the_full_nineteen():
    assert len(REQUIRED_ARTIFACTS) == 19
    assert len(set(REQUIRED_ARTIFACTS)) == 19, "duplicate filename in the list"
    assert all(name.endswith(".csv") for name in REQUIRED_ARTIFACTS)


def test_every_patch_db_table_is_required():
    """A CSV patch_db imports but the pipeline never writes is a silent data loss."""
    from patch_db import TABLES

    missing = sorted(set(TABLES.values()) - set(REQUIRED_ARTIFACTS))
    assert missing == [], f"patch_db.TABLES needs CSVs the pipeline never writes: {missing}"


def test_every_hybrid_chatbot_csv_is_required():
    from hybrid_chatbot.config import CSV_TABLES

    missing = sorted(set(CSV_TABLES) - set(REQUIRED_ARTIFACTS))
    assert missing == [], f"hybrid_chatbot needs CSVs the pipeline never writes: {missing}"


# ── What lands on disk ───────────────────────────────────────────────────────

def test_every_required_artifact_is_written_and_non_empty(out_dir):
    for name in REQUIRED_ARTIFACTS:
        path = out_dir / name
        assert path.exists(), f"{name} was not written"
        assert path.stat().st_size > 0, f"{name} is a zero-byte file"
        assert len(pd.read_csv(path)) > 0, f"{name} has a header but no rows"


def test_returned_frames_cover_exactly_the_required_artifacts(frames):
    assert set(frames) == set(REQUIRED_ARTIFACTS)
    assert all(isinstance(df, pd.DataFrame) for df in frames.values())


def test_written_files_match_the_returned_frames(frames, out_dir):
    for name, df in frames.items():
        assert len(pd.read_csv(out_dir / name)) == len(df), f"{name} row count drifted"


def test_csvs_use_lf_line_endings(out_dir):
    """lineterminator='\\n' — a CRLF rewrite on Windows would change every byte."""
    for name in REQUIRED_ARTIFACTS:
        assert b"\r\n" not in (out_dir / name).read_bytes(), f"{name} has CRLF endings"


# ── Row counts (the cascade) ─────────────────────────────────────────────────

EXPECTED_ROWS = {
    "clean_posts.csv": 8687,
    "clean_with_clusters_v2.csv": 8567,
    "graph_edge_intelligence_table.csv": 285,
    "narrative_intelligence_summary.csv": 154,
    "narrative_spread_chain_table.csv": 154,
    "narrative_topic_mapping.csv": 154,
    "narrative_registry.csv": 154,
    "daily_volume_v2.csv": 201,
    "subreddit_domain_flow_v2.csv": 1337,
    "ideological_distance_matrix.csv": 45,
    "bridge_authors_v2.csv": 178,
    "subreddit_intelligence_summary.csv": 9,
    "user_intelligence_summary.csv": 192,
    "narrative_diffusion_table.csv": 285,
}


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_ROWS.items()))
def test_artifact_row_counts(out_dir, name, expected):
    assert len(pd.read_csv(out_dir / name)) == expected


def test_ideological_matrix_is_built_from_clean_posts(frames, sample_distances):
    """45 pairs either way — only the values reveal the cascade level.

    Built from the 8,567 set instead of the 8,687 set, only 10 of the 45 rows
    would be right.
    """
    got = frames["ideological_distance_matrix.csv"].reset_index(drop=True)
    pd.testing.assert_frame_equal(got, sample_distances.reset_index(drop=True))


def test_daily_volume_is_built_from_the_8567_set(frames):
    assert frames["daily_volume_v2.csv"].post_count.sum() == 8567


def test_domain_flow_is_built_from_the_8567_set(frames):
    """1,337 rows summing to 8,567 minus the 88 null-domain posts."""
    assert frames["subreddit_domain_flow_v2.csv"]["count"].sum() == 8479


# ── Serialization contracts the backend parses ───────────────────────────────

def test_spread_sequence_survives_the_csv_round_trip(out_dir):
    """backend/networkgraph/routers/subreddit.py:390 parses str(list) and silently
    yields nothing for any other shape."""
    reread = pd.read_csv(out_dir / "narrative_spread_chain_table.csv")
    assert len(reread) == 154
    pattern = re.compile(r"^\['.*'\]$")
    bad = [s for s in reread.spread_sequence if not pattern.match(s)]
    assert bad == [], f"spread_sequence lost its str(list) shape: {bad[:3]}"
    assert all(isinstance(ast.literal_eval(s), list) for s in reread.spread_sequence)


def test_subreddit_intelligence_domain_column_round_trips(out_dir):
    """Same str(list) hazard: consumers ast.literal_eval this column."""
    reread = pd.read_csv(out_dir / "subreddit_intelligence_summary.csv")
    for value in reread["domain"]:
        assert isinstance(ast.literal_eval(value), list)


# ── Column contracts the backend queries ─────────────────────────────────────

REQUIRED_COLUMNS = {
    "narrative_intelligence_summary.csv": [
        "narrative_id", "cluster_id", "representative_title", "primary_domain",
        "unique_authors", "unique_subreddits_x", "spread_strength",
        "first_seen_x", "last_seen",
    ],
    "narrative_spread_chain_table.csv": ["narrative_id", "spread_sequence"],
    "narrative_topic_mapping.csv": ["narrative_id", "topic_cluster", "topic_label"],
    "narrative_registry.csv": [
        "narrative_id", "cluster_id", "primary_domain", "representative_title",
        "total_posts", "unique_subreddits", "first_seen",
    ],
    "graph_edge_intelligence_table.csv": [
        "narrative_id", "cluster_id", "author", "subreddit", "title", "domain",
        "created_datetime", "origin_subreddit", "hours_from_origin",
    ],
    "daily_volume_v2.csv": ["created_datetime", "post_count"],
    "ideological_distance_matrix.csv": [
        "subreddit_a", "subreddit_b", "ideological_distance",
    ],
    "echo_chamber_scores.csv": ["subreddit", "lift"],
    "subreddit_domain_flow_v2.csv": ["subreddit", "domain", "count"],
    "bridge_authors_v2.csv": ["author", "subreddit", "post_count"],
    "author_influence_profile.csv": [
        "author", "total_posts", "narratives_transported",
        "avg_ideological_distance_crossed", "total_amplification_generated",
        "final_influence_score",
    ],
    "author_amplification_summary.csv": [
        "author", "total_relative_amplification", "amplification_events",
    ],
    "narrative_diffusion_table.csv": [
        "narrative_id", "subreddit", "author", "order_of_appearance",
        "time_from_origin_hours",
    ],
    "subreddit_intelligence_summary.csv": [
        "subreddit", "total_duplicate_posts", "unique_narratives", "unique_users",
        "avg_score", "domain", "lift",
    ],
    "user_intelligence_summary.csv": [
        "author", "total_duplicate_posts", "unique_narratives",
        "communities_active_in", "first_seen", "last_seen", "most_common_domain",
        "total_posts", "narratives_transported", "avg_ideological_distance_crossed",
        "total_relative_amplification", "final_influence_score",
    ],
    "clean_posts.csv": [
        "id", "created_datetime", "subreddit", "author", "title", "domain",
        "score", "num_comments", "permalink", "url",
    ],
    "clean_with_clusters_v2.csv": ["id", "subreddit", "author", "duplicate_cluster_id"],
}


@pytest.mark.parametrize("name,columns", sorted(REQUIRED_COLUMNS.items()))
def test_required_columns_present(out_dir, name, columns):
    got = list(pd.read_csv(out_dir / name).columns)
    missing = [c for c in columns if c not in got]
    assert missing == [], f"{name} is missing {missing}; has {got}"


# ── The uncategorized-domain worklist ────────────────────────────────────────

def test_uncategorized_domains_worklist_is_written(out_dir):
    """The 41-domain seed leaves most of the 909 domains unresolved; a later
    categorization workflow needs that worklist."""
    path = out_dir / UNCATEGORIZED_DOMAINS_FILE
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    assert lines == sorted(lines), "worklist must be sorted for a stable diff"
    assert len(lines) == len(set(lines))


def test_worklist_holds_only_domains_outside_the_allowlist(out_dir):
    import json

    allowed = json.loads((DATA_DIR / "domain_categories.json").read_text(encoding="utf-8"))
    lines = (out_dir / UNCATEGORIZED_DOMAINS_FILE).read_text(encoding="utf-8").splitlines()
    assert set(lines).isdisjoint(allowed)


def test_worklist_is_not_an_artifact(out_dir):
    assert UNCATEGORIZED_DOMAINS_FILE not in REQUIRED_ARTIFACTS
    assert not UNCATEGORIZED_DOMAINS_FILE.endswith(".csv")


# ── Isolation ────────────────────────────────────────────────────────────────

def test_pipeline_does_not_mutate_its_input(golden_clean_posts, pipeline_run):
    """golden_clean_posts is session-scoped and shared with every other test."""
    assert len(golden_clean_posts) == 8687
    assert list(golden_clean_posts.columns) == [
        "id", "created_datetime", "subreddit", "author", "title", "domain",
        "score", "num_comments", "permalink", "url",
    ]


def test_nothing_is_written_into_the_repo_data_dir(out_dir):
    assert DATA_DIR not in out_dir.parents and out_dir != DATA_DIR
