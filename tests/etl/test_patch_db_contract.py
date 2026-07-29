# tests/etl/test_patch_db_contract.py
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from patch_db import TABLES  # noqa: E402

from etl.config import DATA_DIR  # noqa: E402


def test_bridge_authors_and_media_flow_are_mapped():
    """Both exist in the committed DB but were absent from TABLES, so a
    rebuild-from-CSV dropped them silently."""
    assert TABLES.get("bridge_authors") == "bridge_authors_v2.csv"
    assert TABLES.get("media_flow") == "subreddit_domain_flow_v2.csv"


def test_every_mapped_csv_exists_on_disk():
    missing = [csv for csv in TABLES.values() if not (DATA_DIR / csv).exists()]
    assert missing == [], f"TABLES references CSVs that do not exist: {missing}"


def test_table_names_are_unique_and_csvs_distinct():
    assert len(TABLES) == len(set(TABLES.values())), "two tables share one CSV"


def test_original_nine_mappings_are_unchanged():
    """Guard against an edit that accidentally rewrites the existing contract."""
    expected = {
        "narratives": "narrative_intelligence_summary.csv",
        "topics": "narrative_topic_mapping.csv",
        "chains": "narrative_spread_chain_table.csv",
        "amplification": "author_amplification_summary.csv",
        "daily_volume": "daily_volume_v2.csv",
        "echo_chambers": "echo_chamber_scores.csv",
        "ideological_matrix": "ideological_distance_matrix.csv",
        "posts": "clean_posts.csv",
        "subreddit_edges": "graph_edge_intelligence_table.csv",
    }
    for table, csv in expected.items():
        assert TABLES[table] == csv, f"{table} mapping changed"
