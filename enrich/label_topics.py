"""Replace `LABEL_ME_*` placeholders in `narrative_topic_mapping.csv` with
real, LLM-generated topic labels.

Public surface:
    label_topics(topic_map_df, summary_df, *, llm=None) -> pd.DataFrame

Behavior:
    * `topic_map_df` has columns `narrative_id, topic_cluster, topic_label`
      (one row per narrative; `topic_label` starts out as `LABEL_ME_<n>`).
    * `summary_df` has (among others) `narrative_id` and
      `representative_title`, plus a strength/spread column used to rank
      narratives within a cluster.
    * For each distinct `topic_cluster` id present in `topic_map_df`, the
      top 20 representative titles (by descending strength) are joined into
      one prompt and sent to the LLM in a single call, asking for a 2-4 word
      label.
    * On success, every row in that cluster gets `topic_label` rewritten to
      the returned label. On failure (any exception from `llm`), the
      cluster's rows keep their original `LABEL_ME_<n>` placeholder --
      graceful degradation, never a hard failure for the whole run.
    * The number of clusters is derived from the data (`topic_cluster`
      unique values), never hardcoded.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import pandas as pd

from enrich.llm import generate

logger = logging.getLogger(__name__)

_TOP_N = 20

_PROMPT_TEMPLATE = (
    "Below are 20 headlines from Reddit posts that cluster together as one topic.\n"
    "In 2-4 words, name the shared subject of these headlines.\n\n"
    "{titles}\n\n"
    "Answer with just the label — no quotes, no punctuation, no explanation."
)

# Preference order for the "highest membership" ranking column: the first
# of these present in `summary_df` is used to rank narratives within a
# cluster before taking the top `_TOP_N` representative titles.
_STRENGTH_COLUMNS = ("spread_strength", "total_posts_x", "total_posts")


def _build_prompt(titles: list[str]) -> str:
    bullet_list = "\n".join(f"- {t}" for t in titles)
    return _PROMPT_TEMPLATE.format(titles=bullet_list)


def _clean_label(text: str) -> str:
    return text.strip().strip("\"'`.,!?\n\r ").strip()


def _pick_strength_column(summary_df: pd.DataFrame) -> Optional[str]:
    for candidate in _STRENGTH_COLUMNS:
        if candidate in summary_df.columns:
            return candidate
    return None


def label_topics(
    topic_map_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    *,
    llm: Optional[Callable[[str], str]] = None,
) -> pd.DataFrame:
    """Rewrite `topic_label` for each `topic_cluster`, one LLM call per cluster.

    `llm` defaults to `enrich.llm.generate` and is called as `llm(prompt) ->
    str`. Passing a fake in tests avoids any real network call.
    """
    if llm is None:
        llm = generate

    result = topic_map_df.copy()

    strength_col = _pick_strength_column(summary_df)
    summary_cols = ["narrative_id", "representative_title"]
    if strength_col:
        summary_cols.append(strength_col)

    lookup = topic_map_df[["narrative_id", "topic_cluster"]].merge(
        summary_df[summary_cols], on="narrative_id", how="left"
    )

    for cluster_id in sorted(topic_map_df["topic_cluster"].unique()):
        subset = lookup[lookup["topic_cluster"] == cluster_id]
        if strength_col:
            subset = subset.sort_values(strength_col, ascending=False, na_position="last")
        titles = subset["representative_title"].dropna().head(_TOP_N).tolist()

        if not titles:
            logger.warning(
                "no representative titles for cluster %s; keeping placeholder",
                cluster_id,
            )
            continue

        prompt = _build_prompt(titles)
        try:
            raw_label = llm(prompt)
        except Exception:
            logger.warning(
                "label generation failed for cluster %s; keeping placeholder",
                cluster_id,
            )
            continue

        label = _clean_label(raw_label)
        if not label:
            logger.warning(
                "label generation returned empty text for cluster %s; "
                "keeping placeholder",
                cluster_id,
            )
            continue

        result.loc[result["topic_cluster"] == cluster_id, "topic_label"] = label

    return result
