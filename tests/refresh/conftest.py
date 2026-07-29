"""Shared fixtures for refresh tests: fake artifacts and a scratch git repo."""
import subprocess
from pathlib import Path

import pytest


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


@pytest.fixture()
def good_artifacts(tmp_path: Path) -> Path:
    """A directory populated with all 19 non-empty CSVs plus the datestamp."""
    out = tmp_path / "artifacts"
    out.mkdir()
    for name in REQUIRED:
        (out / name).write_text("id,x\na,1\nb,2\nc,3\n", encoding="utf-8")
    (out / "generated_at.txt").write_text("2026-07-29T00:00:00Z\n", encoding="utf-8")
    return out


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """A fresh git repo we can force-push into without touching anything real."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@ci.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=root, check=True)
    return root
