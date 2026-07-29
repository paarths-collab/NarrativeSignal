"""Tests for `enrich.label_topics.label_topics` -- no real LLM calls."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from enrich.label_topics import label_topics

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def topic_map_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "narrative_topic_mapping.csv")


@pytest.fixture
def summary_df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "narrative_intelligence_summary.csv")


def test_happy_path_all_clusters_get_real_labels(topic_map_df, summary_df):
    """Fake generator returns a fixed label per cluster (in cluster-id order);
    every row's LABEL_ME_* placeholder is replaced with a real label."""
    cluster_ids = sorted(topic_map_df["topic_cluster"].unique())
    fake_labels = ["US Politics", "Middle East", "Economy", "Culture", "Tech"]
    assert len(cluster_ids) == len(fake_labels), (
        "test fixture assumes 5 clusters in the committed dataset"
    )
    labels_by_call_order = iter(fake_labels)
    calls: list[str] = []

    def fake_generate(prompt: str) -> str:
        calls.append(prompt)
        return next(labels_by_call_order)

    result = label_topics(topic_map_df, summary_df, llm=fake_generate)

    assert len(calls) == len(cluster_ids)
    assert not result["topic_label"].astype(str).str.startswith("LABEL_ME_").any()

    expected = dict(zip(cluster_ids, fake_labels))
    for cluster_id, label in expected.items():
        subset = result[result["topic_cluster"] == cluster_id]
        assert (subset["topic_label"] == label).all()


def test_partial_failure_keeps_placeholder_for_failed_cluster(topic_map_df, summary_df):
    """Cluster 3's LLM call raises; its rows keep LABEL_ME_3 while every
    other cluster is labeled normally."""
    cluster_ids = sorted(topic_map_df["topic_cluster"].unique())
    fake_labels = ["US Politics", "Middle East", "Economy", None, "Tech"]
    assert len(cluster_ids) == len(fake_labels)

    call_index = {"n": 0}

    def fake_generate(prompt: str) -> str:
        idx = call_index["n"]
        call_index["n"] += 1
        label = fake_labels[idx]
        if label is None:
            raise RuntimeError("simulated LLM failure for this cluster")
        return label

    result = label_topics(topic_map_df, summary_df, llm=fake_generate)

    failed_cluster = cluster_ids[3]
    assert failed_cluster == 3, "test fixture assumes cluster id 3 is the third-lowest id"
    failed_subset = result[result["topic_cluster"] == failed_cluster]
    assert (failed_subset["topic_label"] == "LABEL_ME_3").all()

    expected_ok = {
        cluster_ids[0]: "US Politics",
        cluster_ids[1]: "Middle East",
        cluster_ids[2]: "Economy",
        cluster_ids[4]: "Tech",
    }
    for cluster_id, label in expected_ok.items():
        subset = result[result["topic_cluster"] == cluster_id]
        assert (subset["topic_label"] == label).all()


def test_column_contract_and_row_count_preserved(topic_map_df, summary_df):
    """Output has exactly [narrative_id, topic_cluster, topic_label], in
    that order, with one row per input narrative (never hardcode 154)."""

    def fake_generate(prompt: str) -> str:
        return "Some Label"

    result = label_topics(topic_map_df, summary_df, llm=fake_generate)

    assert list(result.columns) == ["narrative_id", "topic_cluster", "topic_label"]
    assert len(result) == len(topic_map_df)
    assert set(result["narrative_id"]) == set(topic_map_df["narrative_id"])


def test_idempotent_with_deterministic_fake(topic_map_df, summary_df):
    """Running twice with a deterministic fake (same prompt -> same reply)
    produces byte-identical output both times."""

    def fake_generate(prompt: str) -> str:
        # Deterministic function of the prompt content itself, so identical
        # cluster inputs always produce identical labels across runs.
        return f"Label {len(prompt) % 97}"

    result1 = label_topics(topic_map_df, summary_df, llm=fake_generate)
    result2 = label_topics(topic_map_df, summary_df, llm=fake_generate)

    pd.testing.assert_frame_equal(result1, result2)
    # Original input must not have been mutated by either call.
    assert topic_map_df["topic_label"].astype(str).str.startswith("LABEL_ME_").all()


def test_llm_failure_for_every_cluster_keeps_all_placeholders(topic_map_df, summary_df):
    """If every cluster's call fails, the output is unchanged from the input
    (full graceful degrade, not a raised exception)."""

    def always_fails(prompt: str) -> str:
        raise RuntimeError("boom")

    result = label_topics(topic_map_df, summary_df, llm=always_fails)

    pd.testing.assert_frame_equal(
        result.reset_index(drop=True), topic_map_df.reset_index(drop=True)
    )
