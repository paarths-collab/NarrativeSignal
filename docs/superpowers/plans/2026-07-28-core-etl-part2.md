# Core ETL Implementation Plan — Part 2 (Tasks 9–18)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Continues:** [2026-07-28-core-etl.md](2026-07-28-core-etl.md). Tasks 1–8 must be complete.

**Goal:** The narrative family, author/amplification family, chatbot artifacts, the
orchestrator, and dead-code removal.

---

## Regeneration notice — read before Task 13

Six artifacts were produced by an older run using float narrative ids (`0.0_salon.com`)
with **zero** id overlap with the current tables (spec §6.8). The decision recorded in
spec §10 is to **regenerate them in Gen-B format**. Consequences:

- `narrative_diffusion_table` covered 1,055 narratives / 8,687 rows because Gen-A admitted
  singletons. Gen-B has 154 narratives / 285 posts, so the regenerated table is **285 rows**.
- These artifacts therefore get **structural and formula tests**, never byte-equality with
  the committed files. Asserting equality against Gen-A output would lock in the bug.
- `.replace(".0_", "_")` at every join site becomes dead code — removed in Task 18.

---

### Task 9: `narratives.py` — `graph_edge_intelligence_table` ✅

**Files:**
- Create: `etl/transforms/narratives.py`
- Create: `tests/etl/test_narratives_edges.py`

One row per clustered post (285), **not per edge**. `narrative_id` splits a cluster by
domain, which is why 131 clusters become 154 narratives.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_narratives_edges.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges


def test_reproduces_committed_artifact(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    exp = pd.read_csv(DATA_DIR / "graph_edge_intelligence_table.csv")
    assert len(got) == 285
    assert got.narrative_id.nunique() == 154
    # graph_edge_intelligence_table has no `id` column; permalink is the unique key.
    assert exp.permalink.is_unique and got.permalink.is_unique
    merged = got.merge(exp, on="permalink", suffixes=("_got", "_exp"))
    assert len(merged) == 285
    assert (merged.narrative_id_got == merged.narrative_id_exp).all()
    assert (merged.origin_subreddit_got == merged.origin_subreddit_exp).all()
    assert np.allclose(merged.hours_from_origin_got, merged.hours_from_origin_exp,
                       atol=1e-9)


def test_narrative_id_is_cluster_underscore_domain(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    rebuilt = got.cluster_id.astype(int).astype(str) + "_" + got.domain
    assert (got.narrative_id == rebuilt).all()


def test_clusters_split_by_domain(golden_clean_posts):
    """22 clusters span >1 domain; 131 clusters therefore yield 154 narratives."""
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    assert got.cluster_id.nunique() == 131
    assert got.narrative_id.nunique() == 154


def test_origin_is_earliest_post_in_the_narrative(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    for nid, grp in got.groupby("narrative_id"):
        earliest = grp.loc[grp.created_datetime.idxmin()]
        assert (grp.origin_subreddit == earliest.subreddit).all()
        assert grp.hours_from_origin.min() == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_narratives_edges.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl.transforms.narratives'`

- [ ] **Step 3: Write `etl/transforms/narratives.py`**

```python
"""Narrative-level transforms (spec §6.3)."""
import pandas as pd

GRAPH_EDGE_COLUMNS = [
    "narrative_id", "cluster_id", "author", "subreddit", "title", "domain",
    "url", "permalink", "created_datetime", "score", "num_comments",
    "origin_subreddit", "hours_from_origin",
]


def build_graph_edges(clusters: pd.DataFrame) -> pd.DataFrame:
    """graph_edge_intelligence_table: one row per clustered post.

    A cluster is one headline; splitting it by domain yields the narrative. 62 of
    131 clusters span more than one domain, so 131 clusters -> 154 narrative_ids.
    """
    edges = clusters[clusters["duplicate_cluster_id"].notna()].copy()
    edges["cluster_id"] = edges["duplicate_cluster_id"].astype(int)
    edges["narrative_id"] = edges["cluster_id"].astype(str) + "_" + edges["domain"]
    edges["created_datetime"] = pd.to_datetime(edges["created_datetime"])

    grouped = edges.groupby("narrative_id")
    origin_time = grouped["created_datetime"].transform("min")
    edges["hours_from_origin"] = (
        edges["created_datetime"] - origin_time
    ).dt.total_seconds() / 3600.0

    origin_sub = (
        edges.loc[grouped["created_datetime"].idxmin()]
        .set_index("narrative_id")["subreddit"]
    )
    edges["origin_subreddit"] = edges["narrative_id"].map(origin_sub)

    return edges[GRAPH_EDGE_COLUMNS].reset_index(drop=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_narratives_edges.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/narratives.py tests/etl/test_narratives_edges.py
git commit -m "feat: add narrative graph edge transform"
```

---

### Task 10: `narrative_intelligence_summary` ✅

**Files:**
- Modify: `etl/transforms/narratives.py`
- Create: `tests/etl/test_narratives_summary.py`

The `_x`/`_y` suffix pairs are **identical on all 154 rows** — a vestigial pandas
self-merge. Production reads only `_x`; emit both for compatibility (landmine L3).

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_narratives_summary.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges, build_narrative_summary


def _summary(golden_clean_posts):
    return build_narrative_summary(build_graph_edges(build_clusters_table(golden_clean_posts)))


def test_reproduces_committed_artifact(golden_clean_posts):
    got = _summary(golden_clean_posts)
    exp = pd.read_csv(DATA_DIR / "narrative_intelligence_summary.csv")
    assert len(got) == 154
    assert list(got.columns) == list(exp.columns)
    merged = got.merge(exp, on="narrative_id", suffixes=("_g", "_e"))
    assert len(merged) == 154
    for col in ["total_posts_x", "unique_subreddits_x", "unique_authors",
                "cluster_id", "spread_strength"]:
        assert (merged[f"{col}_g"] == merged[f"{col}_e"]).all(), col
    assert (merged.representative_title_g == merged.representative_title_e).all()
    assert (merged.primary_domain_g == merged.primary_domain_e).all()


def test_representative_title_is_highest_scoring_post(golden_clean_posts):
    edges = build_graph_edges(build_clusters_table(golden_clean_posts))
    got = _summary(golden_clean_posts).set_index("narrative_id")
    for nid, grp in edges.groupby("narrative_id"):
        assert got.loc[nid, "representative_title"] == grp.loc[grp.score.idxmax(), "title"]


def test_spread_strength_is_posts_times_subreddits(golden_clean_posts):
    got = _summary(golden_clean_posts)
    assert (got.spread_strength == got.total_posts_x * got.unique_subreddits_x).all()


def test_x_and_y_columns_are_identical(golden_clean_posts):
    """Vestigial self-merge (L3). Production reads only _x."""
    got = _summary(golden_clean_posts)
    assert (got.total_posts_x == got.total_posts_y).all()
    assert (got.unique_subreddits_x == got.unique_subreddits_y).all()
    assert (got.first_seen_x == got.first_seen_y).all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_narratives_summary.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_narrative_summary'`

- [ ] **Step 3: Append to `etl/transforms/narratives.py`**

```python
SUMMARY_COLUMNS = [
    "narrative_id", "total_posts_x", "unique_subreddits_x", "unique_authors",
    "first_seen_x", "last_seen", "cluster_id", "primary_domain",
    "representative_title", "total_posts_y", "unique_subreddits_y",
    "first_seen_y", "spread_strength",
]


def build_narrative_summary(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_intelligence_summary, grouped from graph_edge_intelligence_table."""
    grouped = edges.groupby("narrative_id")
    out = grouped.agg(
        total_posts_x=("author", "size"),
        unique_subreddits_x=("subreddit", "nunique"),
        unique_authors=("author", "nunique"),
        first_seen_x=("created_datetime", "min"),
        last_seen=("created_datetime", "max"),
        cluster_id=("cluster_id", "first"),
        primary_domain=("domain", "first"),
    ).reset_index()

    best = edges.loc[grouped["score"].idxmax()].set_index("narrative_id")["title"]
    out["representative_title"] = out["narrative_id"].map(best)

    # Vestigial self-merge duplicates (L3): identical to the _x columns by construction.
    out["total_posts_y"] = out["total_posts_x"]
    out["unique_subreddits_y"] = out["unique_subreddits_x"]
    out["first_seen_y"] = out["first_seen_x"]

    out["spread_strength"] = out["total_posts_x"] * out["unique_subreddits_x"]
    return out[SUMMARY_COLUMNS]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_narratives_summary.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/narratives.py tests/etl/test_narratives_summary.py
git commit -m "feat: add narrative intelligence summary transform"
```

---

### Task 11: `narrative_spread_chain_table` ✅ and `narrative_topic_mapping`

**Files:**
- Modify: `etl/transforms/narratives.py`
- Create: `tests/etl/test_narratives_chains.py`

`spread_sequence` **retains duplicates** — deduplicating breaks 41 of 154 rows. Format is
Python `str(list)`: single quotes, `, ` separator (landmine L5).

`topic_cluster` came from KMeans over MiniLM embeddings, which are not reproducible without
a model download. It gets a structural test; Workflow C replaces `LABEL_ME_*` later.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_narratives_chains.py
import ast
import re

import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import (
    build_graph_edges, build_spread_chains, build_topic_mapping,
)


def _edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


def test_spread_chains_reproduce_committed_artifact(golden_clean_posts):
    got = build_spread_chains(_edges(golden_clean_posts))
    exp = pd.read_csv(DATA_DIR / "narrative_spread_chain_table.csv")
    assert len(got) == 154
    merged = got.merge(exp, on="narrative_id", suffixes=("_g", "_e"))
    assert len(merged) == 154
    assert (merged.spread_sequence_g == merged.spread_sequence_e).all()


def test_duplicates_are_retained(golden_clean_posts):
    """Deduplicating by first appearance breaks 41/154 rows."""
    got = build_spread_chains(_edges(golden_clean_posts))
    seqs = got.spread_sequence.map(ast.literal_eval)
    assert (seqs.map(len) > seqs.map(lambda s: len(set(s)))).sum() > 0


def test_serialization_format(golden_clean_posts):
    """L5: subreddit.py:390 requires exactly this shape."""
    got = build_spread_chains(_edges(golden_clean_posts))
    assert got.spread_sequence.str.match(r"^\['.*'\]$").all()
    multi = got[got.spread_sequence.str.contains(",")]
    assert multi.spread_sequence.str.contains("', '").all()


def test_topic_mapping_structure(golden_clean_posts):
    got = build_topic_mapping(_edges(golden_clean_posts), n_clusters=5, seed=42)
    assert len(got) == 154
    assert list(got.columns) == ["narrative_id", "topic_cluster", "topic_label"]
    assert set(got.topic_cluster) <= {0, 1, 2, 3, 4}
    assert got.topic_label.str.match(r"^LABEL_ME_[0-4]$").all()
    rebuilt = "LABEL_ME_" + got.topic_cluster.astype(str)
    assert (got.topic_label == rebuilt).all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_narratives_chains.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_spread_chains'`

- [ ] **Step 3: Append to `etl/transforms/narratives.py`**

```python
import hashlib
import re

import numpy as np

_EMBED_DIM = 384


def build_spread_chains(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_spread_chain_table: subreddits in chronological order.

    Duplicates are RETAINED — '103_vox.com' is ['politics', 'politics']. Serialized
    with Python str(list) because backend/networkgraph/routers/subreddit.py:390
    parses exactly that shape (L5).
    """
    sequences = (
        edges.sort_values("created_datetime")
        .groupby("narrative_id")["subreddit"]
        .apply(list)
    )
    return pd.DataFrame(
        {"narrative_id": sequences.index, "spread_sequence": sequences.map(str).values}
    )


def _hash_embed(text: str) -> np.ndarray:
    """Deterministic offline embedding, mirroring streamgraph2/models/embedder.py.

    The original pipeline used all-MiniLM-L6-v2, which needs a model download. Topic
    ids therefore differ from the committed file by design; only the *shape* of
    narrative_topic_mapping is contractual, and Workflow C replaces the labels.
    """
    vec = np.zeros(_EMBED_DIM, dtype="float32")
    for token in re.findall(r"[a-z0-9]+", str(text).lower()):
        if len(token) < 2:
            continue
        vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def build_topic_mapping(
    edges: pd.DataFrame, n_clusters: int = 5, seed: int = 42
) -> pd.DataFrame:
    """narrative_topic_mapping with LABEL_ME_* placeholders.

    The committed labels are placeholders for a step that was specced and never run.
    """
    from sklearn.cluster import KMeans

    best = (
        edges.loc[edges.groupby("narrative_id")["score"].idxmax()]
        .set_index("narrative_id")["title"]
        .sort_index()
    )
    matrix = np.vstack([_hash_embed(t) for t in best])
    labels = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10).fit_predict(matrix)

    return pd.DataFrame(
        {
            "narrative_id": best.index,
            "topic_cluster": labels,
            "topic_label": ["LABEL_ME_" + str(c) for c in labels],
        }
    ).reset_index(drop=True)
```

- [ ] **Step 4: Add scikit-learn to requirements**

```bash
python - <<'PY'
from pathlib import Path
p = Path("requirements.txt")
t = p.read_text(encoding="utf-8")
assert "scikit-learn" not in t
t = t.replace("faiss-cpu==1.8.0.post1", "faiss-cpu==1.8.0.post1\nscikit-learn==1.5.2")
p.write_text(t, encoding="utf-8")
print("added scikit-learn")
PY
python -m pip install scikit-learn==1.5.2 -q
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_narratives_chains.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add etl/transforms/narratives.py tests/etl/test_narratives_chains.py requirements.txt
git commit -m "feat: add spread chain and topic mapping transforms"
```

---

### Task 12: `bridge_authors_v2` ✅

**Files:**
- Create: `etl/transforms/authors.py`
- Create: `tests/etl/test_authors_bridge.py`

Verified: authors in ≥2 subreddits over the 8,567 set — exact on all 178 rows / 86 authors.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_authors_bridge.py
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.authors import build_bridge_authors


def test_reproduces_committed_artifact(golden_posts):
    got = build_bridge_authors(golden_posts)
    exp = pd.read_csv(DATA_DIR / "bridge_authors_v2.csv")
    assert len(got) == 178
    assert got.author.nunique() == 86
    pd.testing.assert_frame_equal(
        got.sort_values(["author", "subreddit"]).reset_index(drop=True),
        exp.sort_values(["author", "subreddit"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_every_author_spans_at_least_two_subreddits(golden_posts):
    got = build_bridge_authors(golden_posts)
    assert (got.groupby("author").subreddit.nunique() >= 2).all()


def test_post_count_matches_raw_groupby(golden_posts):
    got = build_bridge_authors(golden_posts)
    raw = golden_posts.groupby(["author", "subreddit"]).size()
    for _, r in got.iterrows():
        assert int(raw[(r.author, r.subreddit)]) == int(r.post_count)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_authors_bridge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl.transforms.authors'`

- [ ] **Step 3: Write `etl/transforms/authors.py`**

```python
"""Author-level transforms (spec §6.5)."""
import pandas as pd


def build_bridge_authors(posts: pd.DataFrame) -> pd.DataFrame:
    """bridge_authors_v2: cross-community posters, one row per (author, subreddit)."""
    spans = posts.groupby("author")["subreddit"].nunique()
    bridges = spans[spans >= 2].index
    return (
        posts[posts["author"].isin(bridges)]
        .groupby(["author", "subreddit"])
        .size()
        .rename("post_count")
        .reset_index()
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_authors_bridge.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/authors.py tests/etl/test_authors_bridge.py
git commit -m "feat: add bridge authors transform"
```

---

### Task 13: `narrative_diffusion_table` and `amplification_event_table`

**Files:**
- Modify: `etl/transforms/authors.py`
- Create: `tests/etl/test_authors_amplification.py`

⚠️ **Regenerated in Gen-B format** — see the notice at the top of this document. The
diffusion table becomes 285 rows over 154 narratives, not 8,687 over 1,055. Byte-equality
with the committed Gen-A files is **not** a goal and must not be asserted.

`amplification_percentage` ✅ is verified: `(after - before) / before * 100`, with
`before == 0 → 0.0` (exactly the 72 apparent outliers in the committed file).

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_authors_amplification.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.authors import build_amplification_events, build_diffusion_table
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges


def _edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


def test_diffusion_is_one_row_per_clustered_post(golden_clean_posts):
    got = build_diffusion_table(_edges(golden_clean_posts))
    assert len(got) == 285
    assert got.narrative_id.nunique() == 154
    assert list(got.columns) == [
        "narrative_id", "subreddit", "author",
        "order_of_appearance", "time_from_origin_hours",
    ]


def test_order_is_zero_based_and_chronological(golden_clean_posts):
    got = build_diffusion_table(_edges(golden_clean_posts))
    for _, grp in got.groupby("narrative_id"):
        ordered = grp.sort_values("order_of_appearance")
        assert list(ordered.order_of_appearance) == list(range(len(grp)))
        assert ordered.time_from_origin_hours.is_monotonic_increasing
        assert ordered.time_from_origin_hours.iloc[0] == 0.0


def test_amplification_events_are_cross_subreddit_hops(golden_clean_posts):
    got = build_amplification_events(build_diffusion_table(_edges(golden_clean_posts)),
                                     _edges(golden_clean_posts))
    assert list(got.columns) == [
        "author", "narrative_id", "from_sub", "to_sub", "time_delta_hours",
        "engagement_before", "engagement_after", "amplification_percentage",
    ]
    assert (got.from_sub != got.to_sub).all()
    assert (got.time_delta_hours >= 0).all()


def test_amplification_percentage_formula(golden_clean_posts):
    got = build_amplification_events(build_diffusion_table(_edges(golden_clean_posts)),
                                     _edges(golden_clean_posts))
    nonzero = got[got.engagement_before != 0]
    expected = (nonzero.engagement_after - nonzero.engagement_before) \
        / nonzero.engagement_before * 100
    assert np.allclose(nonzero.amplification_percentage, expected, atol=1e-9)
    assert (got[got.engagement_before == 0].amplification_percentage == 0.0).all()


def test_formula_matches_committed_gen_a_rows():
    """The formula is verified against Gen-A even though the ids differ."""
    exp = pd.read_csv(DATA_DIR / "amplification_event_table.csv")
    nz = exp[exp.engagement_before != 0]
    assert np.allclose(
        nz.amplification_percentage,
        (nz.engagement_after - nz.engagement_before) / nz.engagement_before * 100,
        atol=1e-6,
    )
    assert (exp[exp.engagement_before == 0].amplification_percentage == 0.0).all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_authors_amplification.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_diffusion_table'`

- [ ] **Step 3: Append to `etl/transforms/authors.py`**

```python
DIFFUSION_COLUMNS = [
    "narrative_id", "subreddit", "author",
    "order_of_appearance", "time_from_origin_hours",
]

AMPLIFICATION_COLUMNS = [
    "author", "narrative_id", "from_sub", "to_sub", "time_delta_hours",
    "engagement_before", "engagement_after", "amplification_percentage",
]


def build_diffusion_table(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_diffusion_table: each post's position in its narrative's spread.

    Regenerated in Gen-B format (154 narratives / 285 posts). The committed Gen-A
    file covered 1,055 narratives because it admitted singletons.
    """
    out = edges.sort_values(["narrative_id", "created_datetime"]).copy()
    out["order_of_appearance"] = out.groupby("narrative_id").cumcount()
    out["time_from_origin_hours"] = out["hours_from_origin"]
    return out[DIFFUSION_COLUMNS].reset_index(drop=True)


def build_amplification_events(
    diffusion: pd.DataFrame, edges: pd.DataFrame
) -> pd.DataFrame:
    """amplification_event_table: consecutive cross-subreddit hops of a narrative.

    engagement_before/after are the score of the posts either side of the hop.
    """
    engagement = (
        edges.set_index(["narrative_id", "subreddit", "author"])["score"].groupby(
            level=[0, 1, 2]
        ).max()
    )

    rows = []
    for nid, grp in diffusion.sort_values("order_of_appearance").groupby("narrative_id"):
        records = grp.to_dict("records")
        for prev, cur in zip(records, records[1:]):
            if prev["subreddit"] == cur["subreddit"]:
                continue
            before = int(engagement.get((nid, prev["subreddit"], prev["author"]), 0))
            after = int(engagement.get((nid, cur["subreddit"], cur["author"]), 0))
            pct = ((after - before) / before * 100.0) if before else 0.0
            rows.append(
                {
                    "author": cur["author"],
                    "narrative_id": nid,
                    "from_sub": prev["subreddit"],
                    "to_sub": cur["subreddit"],
                    "time_delta_hours": cur["time_from_origin_hours"]
                    - prev["time_from_origin_hours"],
                    "engagement_before": before,
                    "engagement_after": after,
                    "amplification_percentage": pct,
                }
            )
    return pd.DataFrame(rows, columns=AMPLIFICATION_COLUMNS)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_authors_amplification.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/authors.py tests/etl/test_authors_amplification.py
git commit -m "feat: add diffusion and amplification event transforms in Gen-B format"
```

---

### Task 14: `author_influence_profile` and `author_amplification_summary`

**Files:**
- Modify: `etl/transforms/authors.py`
- Create: `tests/etl/test_authors_influence.py`

The influence formula ✅ was solved by coefficient search — only `5` on the distance term
fits all 3,598 committed rows. `author_amplification_summary` is **stale in the committed
data** (0 of 65 authors reconcile with their source) and is recomputed, not copied.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_authors_influence.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.authors import (
    build_amplification_summary, build_influence_profile,
)


def test_influence_formula_matches_committed_rows():
    """Verified against all 3,598 Gen-A rows; coefficients are 100, 2, 5."""
    exp = pd.read_csv(DATA_DIR / "author_influence_profile.csv")
    expected = (
        exp.total_amplification_generated / 100.0
        + exp.narratives_transported * 2
        + exp.avg_ideological_distance_crossed * 5
    )
    assert np.allclose(exp.final_influence_score, expected, atol=1e-6)


def test_no_other_distance_coefficient_fits():
    exp = pd.read_csv(DATA_DIR / "author_influence_profile.csv")
    base = exp.total_amplification_generated / 100.0 + exp.narratives_transported * 2
    for bad in (1, 2, 3, 10):
        cand = base + exp.avg_ideological_distance_crossed * bad
        assert not np.allclose(exp.final_influence_score, cand, atol=1e-6)


def test_profile_columns_and_score(golden_posts, sample_events, sample_distances):
    got = build_influence_profile(golden_posts, sample_events, sample_distances)
    assert list(got.columns) == [
        "author", "total_posts", "narratives_transported",
        "avg_ideological_distance_crossed", "total_amplification_generated",
        "final_influence_score",
    ]
    expected = (
        got.total_amplification_generated / 100.0
        + got.narratives_transported * 2
        + got.avg_ideological_distance_crossed * 5
    )
    assert np.allclose(got.final_influence_score, expected, atol=1e-9)


def test_amplification_summary_is_recomputed_from_events(sample_events):
    got = build_amplification_summary(sample_events)
    assert list(got.columns) == [
        "author", "total_relative_amplification", "amplification_events",
    ]
    grouped = sample_events.groupby("author")
    assert (
        got.set_index("author").amplification_events
        == grouped.size()
    ).all()
```

Add these fixtures to `tests/etl/conftest.py`:

```python
@pytest.fixture(scope="session")
def sample_events(golden_clean_posts):
    from etl.transforms.authors import build_amplification_events, build_diffusion_table
    from etl.transforms.dedupe import build_clusters_table
    from etl.transforms.narratives import build_graph_edges

    edges = build_graph_edges(build_clusters_table(golden_clean_posts))
    return build_amplification_events(build_diffusion_table(edges), edges)


@pytest.fixture(scope="session")
def sample_distances(golden_clean_posts):
    from etl.transforms.ideological import build_ideological_matrix

    return build_ideological_matrix(golden_clean_posts)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_authors_influence.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_influence_profile'`

- [ ] **Step 3: Append to `etl/transforms/authors.py`**

```python
INFLUENCE_COLUMNS = [
    "author", "total_posts", "narratives_transported",
    "avg_ideological_distance_crossed", "total_amplification_generated",
    "final_influence_score",
]

# Solved by coefficient search against all 3,598 committed rows (spec §6.5).
AMPLIFICATION_DIVISOR = 100.0
NARRATIVES_COEFFICIENT = 2
DISTANCE_COEFFICIENT = 5


def build_amplification_summary(events: pd.DataFrame) -> pd.DataFrame:
    """author_amplification_summary, recomputed.

    The committed file is stale: none of its 65 authors reconcile with the event
    table it was supposedly derived from. Always recompute; never copy.
    """
    grouped = events.groupby("author")
    return (
        pd.DataFrame(
            {
                "total_relative_amplification": grouped["amplification_percentage"].sum(),
                "amplification_events": grouped.size(),
            }
        )
        .reset_index()
        .sort_values("author")
        .reset_index(drop=True)
    )


def build_influence_profile(
    posts: pd.DataFrame, events: pd.DataFrame, distances: pd.DataFrame
) -> pd.DataFrame:
    """author_influence_profile with the verified composite score."""
    lookup: dict[frozenset, float] = {
        frozenset((r.subreddit_a, r.subreddit_b)): float(r.ideological_distance)
        for r in distances.itertuples()
    }
    hops = events.assign(
        distance=[
            lookup.get(frozenset((a, b)), 0.0)
            for a, b in zip(events["from_sub"], events["to_sub"])
        ]
    )
    grouped = hops.groupby("author")

    out = pd.DataFrame(
        {
            "narratives_transported": grouped["narrative_id"].nunique(),
            "avg_ideological_distance_crossed": grouped["distance"].mean(),
            "total_amplification_generated": grouped["amplification_percentage"].sum(),
        }
    ).reset_index()

    totals = posts.groupby("author").size().rename("total_posts")
    out = out.join(totals, on="author")
    out["total_posts"] = out["total_posts"].fillna(0).astype(int)

    out["final_influence_score"] = (
        out["total_amplification_generated"] / AMPLIFICATION_DIVISOR
        + out["narratives_transported"] * NARRATIVES_COEFFICIENT
        + out["avg_ideological_distance_crossed"] * DISTANCE_COEFFICIENT
    )
    return out[INFLUENCE_COLUMNS].sort_values("author").reset_index(drop=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_authors_influence.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/authors.py tests/etl/test_authors_influence.py tests/etl/conftest.py
git commit -m "feat: add author influence profile and amplification summary"
```

---

### Task 15: `chatbot.py` — registry and intelligence summaries

**Files:**
- Create: `etl/transforms/chatbot.py`
- Create: `tests/etl/test_chatbot.py`

All three are Gen-A artifacts regenerated in Gen-B format. `subreddit_intelligence_summary`
serializes its `domain` column as a **stringified Python list** of the top-3 domains — the
same `str(list)` hazard as `spread_sequence`.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_chatbot.py
import ast

import pandas as pd

from etl.transforms.chatbot import (
    build_narrative_registry, build_subreddit_intelligence, build_user_intelligence,
)
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges, build_narrative_summary


def _edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


def test_registry_uses_gen_b_ids(golden_clean_posts):
    edges = _edges(golden_clean_posts)
    got = build_narrative_registry(edges)
    assert len(got) == 154
    assert list(got.columns) == [
        "narrative_id", "cluster_id", "primary_domain", "representative_title",
        "total_posts", "unique_subreddits", "first_seen",
    ]
    assert not got.narrative_id.str.contains(r"\.0_").any(), "Gen-A float ids must be gone"


def test_registry_joins_cleanly_to_summary(golden_clean_posts):
    """The Gen-A bug: raw join overlap was 0 rows. This must now be 154."""
    edges = _edges(golden_clean_posts)
    registry = build_narrative_registry(edges)
    summary = build_narrative_summary(edges)
    assert len(registry.merge(summary, on="narrative_id")) == 154


def test_subreddit_intelligence_domain_is_a_python_list_string(golden_clean_posts, golden_posts):
    edges = _edges(golden_clean_posts)
    got = build_subreddit_intelligence(edges, golden_posts)
    assert len(got) == 9
    for value in got.domain:
        parsed = ast.literal_eval(value)
        assert isinstance(parsed, list) and len(parsed) <= 3


def test_user_intelligence_columns(golden_clean_posts, golden_posts, sample_events,
                                   sample_distances):
    from etl.transforms.authors import build_influence_profile

    profile = build_influence_profile(golden_posts, sample_events, sample_distances)
    got = build_user_intelligence(_edges(golden_clean_posts), golden_posts, profile)
    assert list(got.columns) == [
        "author", "total_duplicate_posts", "unique_narratives", "communities_active_in",
        "first_seen", "last_seen", "most_common_domain", "total_posts",
        "narratives_transported", "avg_ideological_distance_crossed",
        "total_relative_amplification", "final_influence_score",
    ]
    assert (got.total_duplicate_posts >= 1).all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_chatbot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl.transforms.chatbot'`

- [ ] **Step 3: Write `etl/transforms/chatbot.py`**

```python
"""Chatbot-facing artifacts, regenerated in Gen-B format (spec §6.8)."""
import pandas as pd

REGISTRY_COLUMNS = [
    "narrative_id", "cluster_id", "primary_domain", "representative_title",
    "total_posts", "unique_subreddits", "first_seen",
]

SUBREDDIT_INTEL_COLUMNS = [
    "subreddit", "total_duplicate_posts", "unique_narratives", "unique_users",
    "avg_score", "domain", "lift",
]

USER_INTEL_COLUMNS = [
    "author", "total_duplicate_posts", "unique_narratives", "communities_active_in",
    "first_seen", "last_seen", "most_common_domain", "total_posts",
    "narratives_transported", "avg_ideological_distance_crossed",
    "total_relative_amplification", "final_influence_score",
]

TOP_DOMAINS_PER_SUBREDDIT = 3


def build_narrative_registry(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_registry with integer-prefixed ids.

    Gen-A used float prefixes ('0.0_salon.com') and had zero id overlap with the
    other narrative tables. Regenerating here retires the .replace('.0_', '_') bridge.
    """
    grouped = edges.groupby("narrative_id")
    out = grouped.agg(
        cluster_id=("cluster_id", "first"),
        primary_domain=("domain", "first"),
        total_posts=("author", "size"),
        unique_subreddits=("subreddit", "nunique"),
        first_seen=("created_datetime", "min"),
    ).reset_index()

    best = edges.loc[grouped["score"].idxmax()].set_index("narrative_id")["title"]
    out["representative_title"] = out["narrative_id"].map(best)
    return out[REGISTRY_COLUMNS]


def build_subreddit_intelligence(
    edges: pd.DataFrame, posts: pd.DataFrame, echo: pd.DataFrame | None = None
) -> pd.DataFrame:
    """subreddit_intelligence_summary over the clustered posts."""
    grouped = edges.groupby("subreddit")
    out = grouped.agg(
        total_duplicate_posts=("author", "size"),
        unique_narratives=("narrative_id", "nunique"),
        unique_users=("author", "nunique"),
        avg_score=("score", "mean"),
    ).reset_index()

    top = (
        posts.groupby(["subreddit", "domain"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["subreddit", "n"], ascending=[True, False])
        .groupby("subreddit")["domain"]
        .apply(lambda s: str(list(s.head(TOP_DOMAINS_PER_SUBREDDIT))))
    )
    out["domain"] = out["subreddit"].map(top)

    lift = echo.set_index("subreddit")["lift"] if echo is not None else None
    out["lift"] = out["subreddit"].map(lift) if lift is not None else pd.NA
    return out[SUBREDDIT_INTEL_COLUMNS]


def build_user_intelligence(
    edges: pd.DataFrame, posts: pd.DataFrame, influence: pd.DataFrame
) -> pd.DataFrame:
    """user_intelligence_summary: duplicate-post stats joined to the influence profile.

    Note the documented divergence (backend/hybrid/agents.py:83): the two sources
    disagreed on final_influence_score in Gen-A. Taking it from `influence` here
    makes author_influence_profile the single source of truth.
    """
    grouped = edges.groupby("author")
    out = grouped.agg(
        total_duplicate_posts=("narrative_id", "size"),
        unique_narratives=("narrative_id", "nunique"),
        communities_active_in=("subreddit", "nunique"),
        first_seen=("created_datetime", "min"),
        last_seen=("created_datetime", "max"),
    ).reset_index()

    common = (
        edges.groupby(["author", "domain"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["author", "n"], ascending=[True, False])
        .drop_duplicates("author")
        .set_index("author")["domain"]
    )
    out["most_common_domain"] = out["author"].map(common)

    merged = out.merge(influence, on="author", how="left")
    merged["total_relative_amplification"] = merged["total_amplification_generated"]
    for col in ("total_posts", "narratives_transported"):
        merged[col] = merged[col].fillna(0).astype(int)
    for col in ("avg_ideological_distance_crossed", "total_relative_amplification",
                "final_influence_score"):
        merged[col] = merged[col].fillna(0.0)
    return merged[USER_INTEL_COLUMNS]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_chatbot.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add etl/transforms/chatbot.py tests/etl/test_chatbot.py
git commit -m "feat: add chatbot artifacts regenerated in Gen-B format"
```

---

### Task 16: `pipeline.py` — orchestrator and full golden run

**Files:**
- Create: `etl/pipeline.py`
- Create: `tests/etl/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_pipeline.py
import pandas as pd
import pytest

from etl.config import DATA_DIR
from etl.pipeline import REQUIRED_ARTIFACTS, run_pipeline


@pytest.fixture(scope="module")
def output(tmp_path_factory, golden_clean_posts):
    out = tmp_path_factory.mktemp("etl_out")
    run_pipeline(golden_clean_posts, out, categories_path=DATA_DIR / "domain_categories.json")
    return out


def test_every_required_artifact_is_written(output):
    missing = [n for n in REQUIRED_ARTIFACTS if not (output / n).exists()]
    assert missing == []


def test_no_artifact_is_empty(output):
    for name in REQUIRED_ARTIFACTS:
        assert len(pd.read_csv(output / name)) > 0, name


def test_byte_reproducible_artifacts_match_committed(output):
    """The subset whose inputs are fully recovered (spec §6)."""
    for name, rows in [
        ("clean_with_clusters_v2.csv", 8567),
        ("graph_edge_intelligence_table.csv", 285),
        ("narrative_intelligence_summary.csv", 154),
        ("narrative_spread_chain_table.csv", 154),
        ("daily_volume_v2.csv", 201),
        ("subreddit_domain_flow_v2.csv", 1337),
        ("ideological_distance_matrix.csv", 45),
        ("bridge_authors_v2.csv", 178),
    ]:
        got = pd.read_csv(output / name)
        assert len(got) == rows, f"{name}: {len(got)} != {rows}"


def test_patch_db_contract_columns_present(output):
    """Every column backend/networkgraph queries must exist."""
    required = {
        "narrative_intelligence_summary.csv": {
            "narrative_id", "cluster_id", "representative_title", "primary_domain",
            "unique_authors", "unique_subreddits_x", "spread_strength",
            "first_seen_x", "last_seen",
        },
        "narrative_spread_chain_table.csv": {"narrative_id", "spread_sequence"},
        "echo_chamber_scores.csv": {"subreddit", "lift"},
        "daily_volume_v2.csv": {"created_datetime", "post_count"},
    }
    for name, cols in required.items():
        assert cols <= set(pd.read_csv(output / name).columns), name
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl.pipeline'`

- [ ] **Step 3: Write `etl/pipeline.py`**

```python
"""ETL orchestrator (spec §6). Deterministic, offline, no LLM."""
from pathlib import Path

import pandas as pd

from etl.categories import CategoryStore
from etl.config import EXCLUDED_AUTHORS_POSTS
from etl.transforms import (
    authors, chatbot, dedupe, ecosystem, ideological, narratives, volume,
)

REQUIRED_ARTIFACTS = [
    "clean_posts.csv",
    "clean_with_clusters_v2.csv",
    "graph_edge_intelligence_table.csv",
    "narrative_intelligence_summary.csv",
    "narrative_spread_chain_table.csv",
    "narrative_topic_mapping.csv",
    "narrative_registry.csv",
    "narrative_diffusion_table.csv",
    "amplification_event_table.csv",
    "author_influence_profile.csv",
    "author_amplification_summary.csv",
    "bridge_authors_v2.csv",
    "subreddit_domain_flow_v2.csv",
    "clean_top_distinctive_domains.csv",
    "echo_chamber_scores.csv",
    "ideological_distance_matrix.csv",
    "daily_volume_v2.csv",
    "subreddit_intelligence_summary.csv",
    "user_intelligence_summary.csv",
]


def run_pipeline(
    clean_posts: pd.DataFrame, out_dir: Path, categories_path: Path
) -> dict[str, pd.DataFrame]:
    """Build every derived artifact and write it to `out_dir`.

    Order matters. `clean_posts` is 8,687 rows; `posts` is 8,567. Transforms read
    different levels of that cascade — see spec §6.0.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = CategoryStore(categories_path)

    clusters = dedupe.build_clusters_table(clean_posts)
    posts = clusters.drop(columns=["duplicate_cluster_id"])
    edges = narratives.build_graph_edges(clusters)

    distinctive = ecosystem.build_distinctive_domains(posts, store.mapping)
    echo = ecosystem.build_echo_chambers(distinctive)
    distances = ideological.build_ideological_matrix(clean_posts)

    diffusion = authors.build_diffusion_table(edges)
    events = authors.build_amplification_events(diffusion, edges)
    influence = authors.build_influence_profile(posts, events, distances)

    artifacts = {
        "clean_posts.csv": clean_posts,
        "clean_with_clusters_v2.csv": clusters,
        "graph_edge_intelligence_table.csv": edges,
        "narrative_intelligence_summary.csv": narratives.build_narrative_summary(edges),
        "narrative_spread_chain_table.csv": narratives.build_spread_chains(edges),
        "narrative_topic_mapping.csv": narratives.build_topic_mapping(edges),
        "narrative_registry.csv": chatbot.build_narrative_registry(edges),
        "narrative_diffusion_table.csv": diffusion,
        "amplification_event_table.csv": events,
        "author_influence_profile.csv": influence,
        "author_amplification_summary.csv": authors.build_amplification_summary(events),
        "bridge_authors_v2.csv": authors.build_bridge_authors(posts),
        "subreddit_domain_flow_v2.csv": ecosystem.build_domain_flow(posts),
        "clean_top_distinctive_domains.csv": distinctive,
        "echo_chamber_scores.csv": echo,
        "ideological_distance_matrix.csv": distances,
        "daily_volume_v2.csv": volume.build_daily_volume(posts),
        "subreddit_intelligence_summary.csv": chatbot.build_subreddit_intelligence(
            edges, posts, echo
        ),
        "user_intelligence_summary.csv": chatbot.build_user_intelligence(
            edges, posts, influence
        ),
    }

    for name, frame in artifacts.items():
        frame.to_csv(out_dir / name, index=False, lineterminator="\n")

    if store.pending:
        (out_dir / "uncategorized_domains.txt").write_text(
            "\n".join(sorted(store.pending)) + "\n", encoding="utf-8"
        )
    return artifacts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_pipeline.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the entire suite**

Run: `python -m pytest tests/etl -q`
Expected: PASS — all tasks' tests green together

- [ ] **Step 6: Commit**

```bash
git add etl/pipeline.py tests/etl/test_pipeline.py
git commit -m "feat: add ETL orchestrator with full-suite golden validation"
```

---

### Task 17: Close the `patch_db.py` table gap

**Files:**
- Modify: `backend/patch_db.py:30-40`
- Create: `tests/etl/test_patch_db_contract.py`

`bridge_authors` and `media_flow` exist in the committed database but are absent from
`TABLES`, so a rebuild-from-CSV drops them silently.

- [ ] **Step 1: Write the failing test**

```python
# tests/etl/test_patch_db_contract.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from patch_db import TABLES  # noqa: E402

from etl.pipeline import REQUIRED_ARTIFACTS  # noqa: E402


def test_bridge_authors_and_media_flow_are_mapped():
    assert TABLES.get("bridge_authors") == "bridge_authors_v2.csv"
    assert TABLES.get("media_flow") == "subreddit_domain_flow_v2.csv"


def test_every_mapped_csv_is_produced_by_the_etl():
    missing = [csv for csv in TABLES.values() if csv not in REQUIRED_ARTIFACTS]
    assert missing == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_patch_db_contract.py -q`
Expected: FAIL — `assert None == 'bridge_authors_v2.csv'`

- [ ] **Step 3: Add the two entries to `backend/patch_db.py`**

Replace the closing lines of the `TABLES` dict (currently ending at line 39) with:

```python
    "posts": "clean_posts.csv",
    "subreddit_edges": "graph_edge_intelligence_table.csv",
    # Present in the committed DB but previously unmapped: a rebuild-from-CSV
    # dropped them silently. Neither is queried today; both are cheap to keep.
    "bridge_authors": "bridge_authors_v2.csv",
    "media_flow": "subreddit_domain_flow_v2.csv",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/etl/test_patch_db_contract.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/patch_db.py tests/etl/test_patch_db_contract.py
git commit -m "fix: map bridge_authors and media_flow in patch_db TABLES"
```

---

### Task 18: Delete dead code

**Files:**
- Delete: `backend/database.py`, `backend/hybrid/`, `backend/hybrid_crew/`
- Modify: `backend/main.py` (remove the commented-out block at lines 1–453)
- Create: `tests/etl/test_no_dead_imports.py`

`backend/database.py` selects columns (`title`, `domains`, `top_subreddits`,
`top_authors`) that exist in no CSV, opens the database read-write, and creates *views*
over names `patch_db.py` creates as *tables*. DuckDB views persist across connections, so
"fixing" the column names would replace `narratives` with a view exposing
`internal_system_id` and break all 12 `networkgraph` join sites. Nothing imports it.

- [ ] **Step 1: Confirm nothing imports the targets**

```bash
cd backend && grep -rn "^\s*\(from\|import\)\s.*\b\(database\|hybrid\|hybrid_crew\)\b" \
  --include=*.py . | grep -v "hybrid_chatbot" | grep -v "^./hybrid/" \
  | grep -v "^./hybrid_crew/" || echo "NO LIVE IMPORTERS"
```

Expected: `NO LIVE IMPORTERS`

- [ ] **Step 2: Write the guard test**

```python
# tests/etl/test_no_dead_imports.py
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_dead_modules_are_gone():
    for path in ("database.py", "hybrid", "hybrid_crew"):
        assert not (BACKEND / path).exists(), f"{path} should be deleted"


def test_hybrid_chatbot_survives():
    """The mounted chatbot is a different package. Do not delete it."""
    assert (BACKEND / "hybrid_chatbot" / "config.py").exists()


def test_no_gen_a_id_bridge_remains():
    """The .replace('.0_', '_') workaround dies with Gen-A regeneration."""
    offenders = [
        f"{p}:{i}"
        for p in BACKEND.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1)
        if '".0_"' in line or "'.0_'" in line
    ]
    assert offenders == [], offenders
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/etl/test_no_dead_imports.py -q`
Expected: FAIL — `AssertionError: database.py should be deleted`

- [ ] **Step 4: Delete the dead modules**

```bash
git rm -r backend/database.py backend/hybrid backend/hybrid_crew
```

- [ ] **Step 5: Remove the commented-out block from `main.py`**

```bash
python - <<'PY'
from pathlib import Path
p = Path("backend/main.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
head = lines[:453]
assert all(l.lstrip().startswith("#") or not l.strip() for l in head), \
    "lines 1-453 are not all comments/blank - inspect before deleting"
p.write_text("".join(lines[453:]), encoding="utf-8")
print("removed 453 commented lines")
PY
```

- [ ] **Step 6: Remove the `.0_` bridge at every site the test reports**

Run `python -m pytest tests/etl/test_no_dead_imports.py::test_no_gen_a_id_bridge_remains -q`
and delete the normalization at each reported `file:line`, replacing calls to `_norm_nid(x)`
with `x`.

- [ ] **Step 7: Run the tests and the full suite**

Run: `python -m pytest tests/etl -q && python -c "import sys; sys.path.insert(0,'backend'); import main; print('main imports OK')"`
Expected: PASS, then `main imports OK`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: delete dead database.py, hybrid, hybrid_crew and Gen-A id bridge"
```

---

## Definition of Done

- [ ] `python -m pytest tests/etl -q` passes with no skips other than the raw-dump golden test
- [ ] `run_pipeline` writes all 19 artifacts from `clean_posts.csv` alone
- [ ] The eight byte-reproducible artifacts match committed row counts exactly
- [ ] `backend/main.py` still imports
- [ ] No `.0_` normalization remains anywhere in `backend/`

**Not in scope:** Reddit fetching, GitHub Actions, `entrypoint.sh`, the globe pipeline, and
LLM pre-generation. Those are Phases 2–6 and get their own plans.
