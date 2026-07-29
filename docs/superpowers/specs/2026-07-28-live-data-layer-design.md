# Live Data Layer — Design

**Date:** 2026-07-28
**Status:** Approved design, pending implementation plan
**Repo:** `paarths-collab/research-engineering-intern-assignment`

---

## 1. Problem

The dashboards present themselves as a narrative-intelligence platform but serve a
frozen snapshot: 8,687 Reddit posts from 2024-07-23 to 2025-02-18 across 10 political
subreddits, committed to `data/` as 27 pre-computed artifacts (~31 MB).

Two things are missing:

1. **There is no ETL.** No script in the repo turns raw Reddit data into
   `clean_posts.csv` or any of the derived tables. `prompt.md` describes the analysis
   that produced them, but it ran offline and only the outputs were committed.
   `patch_db.py` merely re-imports those CSVs into DuckDB.
2. **There is no scheduler.** No cron, no GitHub Actions, no APScheduler. The only
   refresh path in the entire system is a human clicking the Globe page's pipeline
   button.

The goal is a data layer that pulls real Reddit data daily and presents it through the
existing dashboards unchanged.

---

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Continuity | Rolling window, **7-day cap** | Reddit's API cannot backfill. Each day shows "yesterday's whole picture" across the tracked subreddits. |
| History depth | 7 days | Bucketing hourly gives 168 points — enough baseline for the rolling z-score spike detection. |
| Refresh host | **GitHub Actions** | Free with unlimited minutes on public repos; 7 GB runners versus Render starter's 512 MB. |
| Storage | Orphan branch `data-live`, force-pushed | Git history never grows; old blobs are GC'd. |
| Serving | Stateless container, rebuild at boot | Removes the need for a persistent disk, which is the only thing forcing a paid plan. |
| Cost | **$0/month** | Reddit API, Groq free tier, GH Actions, Render free / Vercel. |
| LLM | Pre-generate common paths nightly | Converts unbounded per-visitor cost into ~40 bounded nightly calls. |
| Globe pipeline | Included, **separate workflow** | 25-minute, ~140-call job must not block the 2-minute deterministic ETL. |

### Why the disk requirement disappears

The 7-day window is exactly reconstructible from the Reddit API at any moment, because
`top?t=week` returns precisely that window. The dataset is never something to preserve —
it is something to re-derive. This also solves cold start: the first run populates a
complete 7-day window immediately.

---

## 3. Architecture

```
GitHub Actions (free, unlimited on public repos)
│
├── Workflow A — core ETL          ~2 min,   0 LLM calls
│     asyncpraw → normalize → transforms → 16 CSVs → validate
│
├── Workflow B — globe pipeline    ~25 min,  ~140 Groq calls
│     9-layer orchestrator → global_events_<date>.json + globe.duckdb
│
└── Workflow C — LLM pre-generation ~3 min,  ~40 Groq calls
      topic labels, domain categories, executive brief, top-20 narratives
                    │
                    └── force-push artifacts → orphan branch `data-live`

Render container (free tier, stateless)
│
└── entrypoint.sh
      ├── curl artifacts from data-live → $DATA_PATH
      ├── on failure: fall back to baked-in /app/seed_data   [exists today]
      ├── rm -f posts_clustered.parquet                      [cache invalidation]
      ├── patch_db.py → rebuild analysis_v2.db               [exists today]
      └── uvicorn
```

Workflows are independent: B failing must never block A. C is non-blocking — if LLM
generation fails, artifacts publish with TF-IDF fallback labels.

### Picking up new data on a warm container

Free-tier spin-down does most of the work: an idle service cold-boots on the next
request and pulls fresh artifacts. For containers that stay warm past an update, an
hourly check compares an etag on `data-live`; on change it re-downloads, builds
`analysis_v2.staging.db`, `os.replace()`s it into position, and resets in-process caches.

The swap is safe because `networkgraph/database.py:34` opens a fresh read-only
connection per request — the next request gets the new file, and in-flight connections
keep their old inode until closed.

**Cache invalidation is mandatory, not optional.** `polarize_1` loads its entire dataset
at import time (`main.py:521`), and four module-level DataFrames never expire
(`streamgraph2/routers/cluster.py:142`, `streamgraph2/data/db.py:335`,
`backend/database.py:17-18`, plus the `posts_clustered.parquet` file cache). Without
invalidation a warm container serves last week's data indefinitely.

---

## 4. Artifact inventory

Verified by column-level audit of every endpoint and frontend component.

### 4.1 Required (16 CSVs + 3 non-CSV)

| Artifact | Consumers | Required columns |
|---|---|---|
| `clean_posts.csv` | polar, stream, intelligence, chatbot | `id, created_datetime, subreddit, author, title, domain, score, num_comments, permalink, url` |
| `clean_with_clusters_v2.csv` | `backend/database.py` semantic search, chatbot index | above + `duplicate_cluster_id` |
| `narrative_intelligence_summary.csv` | intelligence (all endpoints), chatbot | `narrative_id, cluster_id, representative_title, primary_domain, unique_authors, unique_subreddits_x, spread_strength, first_seen_x, last_seen` |
| `narrative_topic_mapping.csv` | intelligence `LEFT JOIN topics` | `narrative_id, topic_cluster, topic_label` |
| `narrative_spread_chain_table.csv` | overlay, neighbors, details, ecosystem | `narrative_id, spread_sequence` |
| `graph_edge_intelligence_table.csv` | **every** intelligence endpoint, chatbot | `narrative_id, cluster_id, author, subreddit, title, domain, url, created_datetime, score, num_comments, origin_subreddit` |
| `daily_volume_v2.csv` | intelligence timeline, stream volume, chatbot | `created_datetime, post_count` |
| `echo_chamber_scores.csv` | polar, stream ecosystem, chatbot | `subreddit, lift` |
| `subreddit_domain_flow_v2.csv` | polar, chatbot **vocabulary** | `subreddit, domain, count` |
| `clean_top_distinctive_domains.csv` | polar | all 9: `subreddit, domain, count, category, sub_total, domain_total, p_domain_given_sub, p_domain_global, lift` |
| `author_amplification_summary.csv` | graph, ecosystem, leaderboard | `author, total_relative_amplification, amplification_events` |
| `bridge_authors_v2.csv` | stream topic-analysis | `author` (+ `subreddit, post_count` unread) |
| `narrative_registry.csv` | chatbot SQL join | per `sql_engine.py:192` |
| `narrative_diffusion_table.csv` | chatbot SQL (2 templates) | per `sql_engine.py:191,206` |
| `author_influence_profile.csv` | chatbot dataset context | `final_influence_score` + identity |
| `subreddit_intelligence_summary.csv` | chatbot SQL | per `sql_engine.py:268,272` |
| `global_events_<date>.json` | **globe page + perspective presets** | schema in §4.3 |
| `globe.duckdb` | globe `/events/{id}/analyze`, **geo cache** | `raw_posts, geo_resolutions, structured_events, news_articles, event_clusters` |
| `ai_cache.json` | polar, globe, stream, perspective | shared LLM cache |

**Two databases, not one.** Beyond `analysis_v2.db` (built by `patch_db.py` from 9 CSVs),
`hybrid_chatbot/config.py:37` points at a second file, `data/hybrid_chatbot.duckdb`, which
`sql_engine.py:67-78` builds at **startup** from its own 12-CSV `CSV_TABLES` map. All 12
are already listed above, so the inventory is complete — but `sql_engine.py:70-72` only
*logs a warning* on a missing CSV, so the chatbot degrades silently rather than failing.
The ETL must satisfy both consumers.

Also absent from `patch_db.py`'s `TABLES` but present in the committed DB: `bridge_authors`
(← `bridge_authors_v2.csv`) and `media_flow` (← `subreddit_domain_flow_v2.csv`). Neither is
queried by any code today, so this is latent rather than broken — but a rebuild-from-CSV
drops them silently. Two lines in `patch_db.py:30-40` close the gap.

### 4.2 Confirmed dead — do not regenerate

| Artifact | Size | Finding |
|---|---|---|
| `ideological_distance_matrix.csv` | 1.8 KB | Loaded into DuckDB as `ideological_matrix`, never queried. Polar's `/similarity` computes from `flow_vectors` instead. |
| `user_intelligence_summary.csv` | 19 KB | Referenced only by unmounted `backend/hybrid/`. |
| `amplification_event_table.csv` | 522 KB | Referenced only by unmounted `backend/hybrid/`. |
| `hybrid_vector.index` + `.jsonl` | 3.7 MB | `EMBED_MODEL_NAME` defaults to `"disabled"`; `sentence-transformers` is not in `requirements.txt`. Cannot be rebuilt in production and is not on any runtime path. |

### 4.3 `global_events_<date>.json` schema

```jsonc
{
  "run_id": "string", "generated_at": "ISO8601 UTC", "event_count": 22,
  "events": [{
    "id": "uuid", "event_id": "uuid", "title": "string",
    "locations": [{"name","lat","lon","geo_type","display_name"}],  // always 1
    "timestamp": "ISO8601", "impact_score": 0.0,
    "sentiment": "negative|neutral|mixed|positive", "sentiment_score": -1.0,
    "risk_level": "Low|Medium|High", "subreddit": "string",
    "reddit_post_ids": ["string"],
    "reddit_metrics": {"total_score","total_comments","post_count","subreddits"},
    "news_sources": [{"title","url","source","trusted"}],  // max 5
    "confidence": "Low|Medium|High", "summary": "string",
    "strategic_implications": ["string"], "last_updated": "ISO8601"
  }]
}
```

`lat`/`lon` must be finite or the frontend drops the pin
(`frontend/app/globe/page.jsx:215`). Only `risk_level` drives pin colour.

### 4.4 Deferred — not on any live code path

`title_embeddings_v2.npy` and `posts_clustered.parquet` feed only stream components that
are **not mounted**. Only `NarrativeTimelineChart` is imported by the stream page;
`StreamgraphChart`, `SpikeAnalysisPanel`, `SentimentTrend`, `TopicClusters`,
`NewsSourcesTable`, `ExecutiveBrief`, and `TopicSelector` are dead. The live stream page
runs entirely off `clean_posts.csv` + `bridge_authors_v2.csv`.

Dedupe clustering computes embeddings in memory, so the `.npy` need not be persisted.
Regenerate both only when re-enabling those panels — and see landmine L1 first.

---

## 5. Schema landmines

Each of these fails **silently**. They are the reason this design needs a golden-master
test rather than a smoke test.

**L1 — `.npy` positional alignment. Confirmed live: 120 posts currently hold the wrong
embedding.** `title_embeddings_v2.npy` is `(8567, 384)` float32, L2-normalized, and aligns
positionally to the **8,567-row `clean_with_clusters_v2` set** — *not* to the 8,687-row
`clean_posts.csv` it is zipped against at `cluster_pipeline.py:100-104`, which truncates to
`min(len)`. The 120-row difference is exactly the `[deleted]`-author rows dropped at
clustering, and because they are interleaved rather than trailing, the misalignment
propagates from the first one onward. Also `db.py:353-354`.

Fix by carrying an explicit `id` array beside the matrix and joining on it. Note the
original embedder was `all-MiniLM-L6-v2` (verified: unit-norm rows), whereas
`streamgraph2/models/embedder.py:18` ships MD5 hash-embedding and `sentence-transformers`
is not in `requirements.txt` — the two are not interchangeable. Since §6.2 dedupe needs no
embeddings at all, this only affects the 5-way topic KMeans.

**L2 — hardcoded cluster labels.** `cluster_pipeline.py:31-37` maps KMeans clusters 0–4
to `US Politics, Geopolitics, Economy, Culture, Technology`, justified only by
`random_state=42` on the old dataset. On new data cluster 0 will not be "US Politics".
Labels still render; they are simply wrong. Labels must be derived, never hardcoded.

**L3 — `_x`/`_y` merge suffixes are load-bearing.** Code reads **only** the `_x`
variants (`utils.py:56,72,75,95`; `narratives.py:99`; `search.py:69`; `subreddit.py:23`).
If a regenerated merge swaps left/right frames, `unique_subreddits_x` silently carries
the wrong value. If the merge produces no collision, the columns vanish and every
intelligence endpoint 500s. **Emit these exact names deliberately.**

**L4 — `narrative_id` normalization.** `_norm_nid` (`utils.py:27`) does
`str(nid).replace(".0_", "_")` to bridge `narrative_registry`'s `0.0_salon.com` against
`narrative_intelligence_summary`'s `0_salon.com`. `chains`, `topics`, and
`subreddit_edges` must use the **normalized** form, because `search.py:80` joins raw
strings without normalizing. Ids are interpolated into SQL after only
`.replace("'", "''")`.

**L5 — `spread_sequence` has two incompatible parsers.** `utils.py:30-42` uses
`ast.literal_eval` with a comma-split fallback (tolerant). But `subreddit.py:390` does a
raw SQL string transform requiring **exactly** single-quoted Python-list form with
`, ` separators: `['democrats', 'politics']`. Emitting `["a","b"]` or `['a','b']` breaks
`/subreddit/{s}/ecosystem` only, while `/overlay` and `/neighbors` keep working.

**L6 — `int(row["count"])` on raw CSV strings.** `data_loader.py:97,114` parse with
`int()`, so `"3.0"` or `""` raises `ValueError`. Because `main.py:680-683` swallows the
exception and mounts an **empty FastAPI app at bare `/api`**, one malformed row 404s
*every* polar endpoint with only a log line. Write true integers.

**L7 — `_threshold_counts` scales with MAX.** `utils.py:112` builds `range(1, max+1)`
levels, one SQL aggregate per level. Live data with higher author counts produces a
proportionally huge SELECT. `/spread-levels` and `/subreddit-reach` will crawl or fail.
**Cap the level count.**

**L8 — `score` must be numeric and not all-NaN per day.** `routers/cluster.py:181,203`
and `db.py:787` call `.idxmax()`, which raises on an all-NaN group. This crashes
`/timeline-volume` — the stream page's only data source.

**L9 — timestamp format.** Every pandas reader infers ISO, but `ingest.py:32` tries
`"%d-%m-%Y %H:%M"` first. Emit `YYYY-MM-DD HH:MM:SS` everywhere.

**L10 — cross-file string identity.** `subreddit` and `domain` values must match
byte-for-byte between `clean_posts.csv` and `subreddit_domain_flow_v2.csv`
(`compute.py:192`, `data_loader.py:83`). Inconsistent `www.` stripping produces empty
panels, never errors. Subreddits are bare names, case-sensitive, no `r/` prefix — the
frontend adds it.

**L11 — parquet cache never invalidates.** `cluster_pipeline.py:86-87` returns the
cached parquet without checking CSV mtime. Delete it on refresh.

**L12 — `backend/database.py` conflicts with `patch_db.py`.** It opens `analysis_v2.db`
**read-write** and issues `CREATE OR REPLACE VIEW narratives …` over names `patch_db`
created as tables. DuckDB refuses to replace a table with a view, and the referenced
columns (`title`, `domains`, `top_subreddits`, `step_number`, `minutes_to_jump`) exist in
no current CSV. A read-write open here can also block the read-only connections.

**L13 — mock data masks total failure.** `events.py:80-165` serves six hardcoded events
(Tel Aviv, Tehran, Kyiv, Beijing) when the events file is missing. The globe renders
convincingly with fabricated geopolitics, and Perspective builds personas on top of them.
**A staleness assertion on `generated_at` is mandatory** — endpoint success proves
nothing.

**L14 — Nominatim policy.** User-Agent carries no contact address
(`utils/geocoder.py:56`), violating OSM policy for automated use. Worse,
`events.py:369-379` reverse-geocodes on **every map click**. The geo cache is an
in-process dict that dies on restart. Fix the User-Agent, persist `globe.duckdb`'s
`geo_resolutions` table across runs, and remove request-time geocoding.

**L15 — `os` is never imported in `sql_engine.py:57`.** The `os.getenv` call raises
`NameError`, swallowed by a bare `except`, so read-only-filesystem detection has never
worked.

**L16 — `category` is a mismatched soft enum.** Data uses `Advocacy / Org`;
`PolarizeTreemap.jsx:26-32` expects `News, Blogs, Advocacy, Research, Government`;
`SourceDetailModal.jsx:50-56` and `main.py:657-662` use a third set. Two conflicting
unknown-defaults (`compute.py:118` "Uncategorized" vs `analytics.py:29` "Media / News").
Domain categorization is assigned to Workflow C.

**L17 — `SimilarityHeatmap` hardcodes `maxValue: 100`** (`SimilarityHeatmap.jsx:94`)
against unbounded shared-domain counts. Denser live data saturates the scale flat.

**L18 — `narrative_diffusion` filter drops everything without news keys.**
`orchestrator.py:109-116` drops events with zero news articles unless engagement ≥ 500.
No news keys means near-zero clusters, `status="complete"`, no new file, and
`load_latest_output()` serves yesterday's forever.

---

## 6. Transforms

All deterministic. No LLM, no network.

> **Provenance.** The original ETL is unrecoverable — it lived outside the repo at a path
> that no longer exists (proven by absolute paths baked into the dead DuckDB views in
> `analysis_v2.db`) and was never committed. `SimPPl.ipynb` is an 18-cell exploratory
> scratchpad over a 535-post single-subreddit sample; it writes no artifacts and its
> KMeans cell was aborted with `KeyboardInterrupt`. It is **not** the pipeline.
>
> Every formula below was instead recovered numerically — solved against the committed
> CSVs until it reproduced them. Entries marked ✅ verified at **0 mismatches on all rows**.

### 6.0 The row-count cascade — get this wrong and everything downstream shifts

| Stage | Rows | Filter |
|---|---|---|
| `data.jsonl` | 8,799 | — |
| **`clean_posts.csv`** | **8,687** | drop `author == 'AutoModerator'` (exactly 112) ✅ |
| **`posts`** = `clean_with_clusters_v2` = `.npy` | **8,567** | additionally drop `author == '[deleted]'` (exactly 120) ✅ |

Different transforms read **different levels of this cascade**. This is the single
easiest thing to get wrong:

- `ideological_distance_matrix` → built from **8,687** (909 domains). Building it from the
  8,567 set gives 899 domains and only **10/45 rows correct**.
- `daily_volume_v2`, `subreddit_domain_flow_v2` → built from **8,567**. `daily_volume`
  matches the 8,567 groupby on 201/201 days, but `clean_posts` on only 130/201.

### 6.1 `normalize.py` → `clean_posts.csv`

`pd.json_normalize` the `data` object of each `t3` record, drop `AutoModerator`, project 10
columns. `created_datetime = pd.to_datetime(created_utc, unit="s")`, naive UTC, formatted
`%Y-%m-%d %H:%M:%S`.

**`domain` is Reddit's own server-computed field — copy it verbatim.** It is *not*
`urlparse(url).netloc`, which differs on **4,940 of 8,687 rows** (57%). Reddit emits
`self.<Subreddit>` for text posts, `i.redd.it`/`v.redd.it` for native media, bare host
otherwise. Read `submission.domain`; never recompute it. `[deleted]` authors pass through
unchanged at this stage.

### 6.2 `dedupe.py` → `clean_with_clusters_v2.csv` ✅ EXACT

**There is no similarity threshold. Clusters are groups of byte-identical titles posted
by at least two different authors.** Fully reproduced — identical post-id set:

```python
# NOTE: runs on clean_posts (8,687) — BEFORE the [deleted] filter
g = clean_posts.groupby("title")
clustered = clean_posts[(g["id"].transform("size")    > 1) &
                        (g["author"].transform("nunique") > 1)]     # 131 groups / 286 posts
# cluster ids assigned in ALPHABETICAL title order (pandas groupby sorts keys)  ✅ 131/131
clustered["duplicate_cluster_id"] = factorize_alphabetically(clustered["title"])
posts = clustered[clustered.author != "[deleted]"]                  # → 285 posts / 131 clusters ✅
```

The **≥2 distinct authors** condition is the whole discriminator: of the 243 non-self
exact-title groups, all 112 excluded have exactly one author and all 131 kept have two or
more — perfect separation, no exceptions either way. Semantically it is the right rule: a
narrative is one headline posted by *different people*. One person crossposting themselves
is not spread. Excluded single-author groups have a median time span of **70 seconds**
versus 5 hours for kept ones — they are near-simultaneous self-crossposts.

A self-post exclusion is **redundant** — 0 of the 131 surviving groups contain a
`self.*` domain, because identical self-post titles are always by the same author.

Two consequences of the ordering:
- Clustering **precedes** the `[deleted]` filter, so one cluster (id 83) is left a
  singleton when its only sibling is dropped. `duplicate_cluster_id` is therefore *not* a
  guarantee of ≥2 surviving posts. Do not assume it downstream.
- No embeddings are required for this step at all. The earlier "embed + cosine threshold +
  union-find" description was wrong on every count.

### 6.3 `narratives.py` → 4 artifacts

`graph_edge_intelligence_table` (285 rows, **one row per post, not per edge**) is the
parent; the other three derive from it grouped by `narrative_id`.

```python
narrative_id      = f"{cluster_id}_{domain}"          # ✅ 131 clusters → 154 narrative_ids
origin_subreddit  = subreddit of earliest post in group                        # ✅
hours_from_origin = (created_datetime - group.min()) / 3600                    # ✅
```

**22** clusters span multiple domains (21 with two, 1 with three), so 109 + 21×2 + 1×3 =
154. That expansion is exactly why every Gen-B narrative table has 154 rows. (62 clusters
span multiple *URLs* — a different figure that must not be substituted here.)

`narrative_intelligence_summary` ✅ all columns verified:
```python
total_posts = count(*);  unique_subreddits = nunique(subreddit);  unique_authors = nunique(author)
first_seen = min(created_datetime);  last_seen = max(created_datetime)
representative_title = arg_max(title, score)          # title of the highest-scoring post
spread_strength      = total_posts * unique_subreddits
```
The `_x`/`_y` suffix pairs are **100% identical** on all 154 rows — a vestigial pandas
self-merge. Production reads only `_x`. Emit both for byte-compatibility (L3).

`narrative_spread_chain_table` ✅ — **duplicates are retained**:
```python
df.sort_values("created_datetime").groupby("narrative_id")["subreddit"].apply(list)
# serialized via Python str(list):  "['democrats', 'politics']"
```
Deduplicating by first appearance produces **41 mismatches** (e.g. `103_vox.com` is
`['politics', 'politics']`). The earlier "ordered by first appearance" claim was wrong.

`narrative_topic_mapping` ✅ — `topic_label = "LABEL_ME_" + str(topic_cluster)`, with
`topic_cluster ∈ {0..4}` from `KMeans(n_clusters=5, random_state=42)` over representative-
title embeddings. **These are placeholders; no LLM ever ran.** Workflow C completes work
that was specced and abandoned, rather than reproducing lost behaviour.

### 6.4 `ecosystem.py` → 3 artifacts

`subreddit_domain_flow_v2` ✅ — `groupby(["subreddit","domain"]).size()` over the 8,567 set.

`clean_top_distinctive_domains` ✅ formulas, ⚠️ hardcoded universe:
```python
p_domain_given_sub = count / sub_total
p_domain_global    = domain_total / 3213      # 3213 = sum of all sub_total
lift               = p_domain_given_sub / p_domain_global
```
Selection is **top-5 per subreddit by lift, minimum `count >= 5`** (5×8 + 2 = 42 rows;
worldpolitics yields only 2). `count` matches a raw `(subreddit, domain)` groupby over the
**8,567** set exactly (42/42).

⚠️ **`sub_total` is the one input that cannot be recovered.** It counts only posts whose
domain is in a hand-curated allowlist of recognized media/advocacy/video/government
domains — an **allowlist, not a blocklist**, and far larger than the 41 domains CTDD
happens to expose (those are only the top-5 per subreddit). Proven by arithmetic:

```
worldpolitics sub_total = 28  = youtu.be(17) + youtube.com(11)
    excluded: i.redd.it(759), a/b/c.l3n.co(101), redgifs.com(8), self.*(73)
Anarchism sub_total = 101     = youtube.com(31) + youtu.be(28) + currentaffairs.org(15)
                              + blackrosefed.org(11) + freedomnews.org.uk(8)
                              + theanarchistlibrary.org(8)
```

Anarchism keeps 101 of its 394 link posts while politics keeps 744 of 990 — the list
recognizes news outlets and ignores image hosts, link shorteners, and personal blogs.
`PoliticalDiscussion` is absent entirely (all 116 of its posts are self-posts).
Categories are `Media / News`, `Advocacy / Org`, `Video`, `Government`. Global total 3,213.

**Consequence for testing:** the formulas above are verified, but `clean_top_distinctive_domains`
and `echo_chamber_scores` **cannot be byte-reproduced** — their input dict is lost. These
two artifacts get *formula-level* golden tests (given a fixed dict, does the arithmetic
match?) rather than byte-equality. This is not a workaround: §10.1 replaces the dict by
design, so exact historical reproduction was never the goal for these two.

`echo_chamber_scores` ✅ verified to full float precision on all 9 rows:
```python
clean_top_distinctive_domains.groupby("subreddit")["lift"].mean()
```
Because it averages an already-top-5-truncated list, the score is highly sensitive to how
many domains clear the `count >= 5` gate — worldpolitics scores 9.19 off just two domains.

### 6.5 `authors.py` → 3 artifacts

`bridge_authors_v2` — authors active in ≥2 subreddits, one row per (author, subreddit).
178 rows / 86 authors.

`author_influence_profile` ✅ — **fully reproduced, all 6 columns at 3,598 of 3,598.**
Originally assumed unrecoverable (Gen-A); the whole construction was later solved:

```python
# universe = the diffusion table's authors, NOT the event table's
total_posts                      = per-author row count in the post table
narratives_transported           = diffusion.groupby(author).narrative_id.nunique()
avg_ideological_distance_crossed = mean pairwise distance across the DISTINCT
                                   subreddits the author posts in; 0.0 below two
total_amplification_generated    = per-author event sum, 0.0 where no events
final_influence_score            = (total_amplification_generated / 100.0
                                    + narratives_transported * 2
                                    + avg_ideological_distance_crossed * 5)
```

Two traps here, both of which produced plausible wrong answers before being caught:

- **The universe is the diffusion table, not the events table.** 1,767 of 3,598 authors
  appear in no amplification event yet carry a nonzero `narratives_transported`. Deriving
  from events alone under-covers by ~3× and fails silently — the score formula still
  validates against whatever rows it is handed.
- **`avg_ideological_distance_crossed` is a *bridge span*, not a hop average.** It is the
  mean pairwise distance across the subreddits an author posts in — nothing to do with the
  hops in the event table. Decisive evidence: exactly 87 authors post in ≥2 subreddits and
  exactly 87 have a nonzero value, and **8 of those 87 appear in no event at all**.

Coefficients 1, 2, 3, 10 on the distance term all fail; only **5** fits every row. 3,511 of
3,598 authors score 0.0 on that term, which is why it hides.

`amplification_percentage` ✅ — `(after - before) / before * 100`, with `before == 0 → 0.0`
(exactly the 72 apparent outliers).

⚠️ **`author_amplification_summary.csv` is stale and must be recomputed, not copied.** Its
65 authors all exist in `amplification_event_table`, but the summed
`total_relative_amplification` matches for **0 of 65**.

### 6.6 `volume.py` → `daily_volume_v2.csv`

```python
posts.groupby(posts["created_datetime"].dt.date).size()      # ✅ 201/201 days
```
Calendar-day bins on naive UTC, over the **8,567** set. **Only days with ≥1 post are
emitted** — 201 rows across a 211-day range, so 10 days are silently absent. The earlier
"one row per calendar day" claim was wrong.

This is precisely what corrupts the row-based rolling window at `db.py:498-502`. Since the
live window is 7 days, a single missing day is a 14% baseline distortion. **Reindex to a
complete date range with zero-fill at read time**, or accept inflated z-scores.

### 6.7 `ideological.py` → `ideological_distance_matrix.csv`

✅ 45/45 exact, max error 0.0:
```python
M = clean_posts.pivot_table(index="subreddit", columns="domain",
                            values="id", aggfunc="count").fillna(0)   # raw counts
ideological_distance = 1 - cosine_similarity(M[a], M[b])
```
All 10 subreddits → C(10,2) = 45 unordered pairs, upper triangle, `subreddit_a` <
`subreddit_b` alphabetically. Built from **8,687** (see §6.0). Binary and log1p vectors are
decisively ruled out (max error 0.84 and 0.72).

Currently loaded into DuckDB and never queried (§4.2) — regenerate only if a consumer appears.

### 6.8 `chatbot.py` → `narrative_registry`, `narrative_diffusion_table`, `subreddit_intelligence_summary`

⚠️ **Gen-A used float ids** (`0.0_salon.com`) against the current integer form
(`0_salon.com`), so the raw `narrative_id` overlap is **zero rows**. Production bridges with
`str(nid).replace(".0_", "_")` (L4).

**Correction — Gen-A's 1,055 narratives are not a different clustering.** Measured: only
**154** of those rows carry a float cluster id; the other **901** have `cluster_id == domain`
and ids of the form `11alive.com_11alive.com`. Gen-A ran its aggregation over all 8,687
clean posts and gave every *unclustered* post a `domain_domain` pseudo-narrative. Its 154
real rows match the regenerated registry on **all 7 columns** after `.0_` → `_`. So the id
format changed and nothing else did — the earlier claim that Gen-A "did not require ≥2
posts" was wrong.

All three artifacts were subsequently reconciled exactly (see §6.8 notes): registry
1055/1055 on every column, `subreddit_intelligence_summary` 9/9 on all 7, and
`user_intelligence_summary`'s duplicate-post half 192/192.

Two rules only visible through that reconciliation, both load-bearing:
- Registry `representative_title` is the **first post in table order**, not the max-score
  post `narrative_intelligence_summary` uses. Max-score fits only 816/1055. The two tables
  genuinely disagree on titles for the same narrative — preserved deliberately.
- `most_common_domain` and the top-3 `domain` list break ties **alphabetically**;
  `value_counts()` ordering fits only 183/192 and 6/9 respectively.

`subreddit_intelligence_summary`: `domain` is a **stringified Python list** of the top-3
domains — same `str(list)` hazard as `spread_sequence`.

### Coverage

`clean_posts.csv` from §6.1, plus 15 derived across §6.2–6.8 = all 16 required CSVs.

---

## 7. Testing

### Golden-master test — the primary correctness mechanism

Run the new ETL over the committed `clean_posts.csv` and assert it reproduces the
committed derived CSVs. 8,687 real posts with known-correct outputs already sit in git —
and `_original_pipeline/data.jsonl` provides the 8,799-record raw input *upstream* of them,
so the fetcher's normalization can be tested end-to-end too.

This does three jobs: proves the recovered formulas correct, guards every future
regression, and lets the entire ETL be built and validated **before writing any
Reddit-fetching code**.

Every ✅ formula in §6 already passes this test at 0 mismatches. The golden master's job is
now to *keep* them passing — plus pin the two that remain unsolved (§6.2 dedupe subset,
`author_amplification_summary`).

### Supporting tests

- **Schema contract**: generated columns match the audited required-column matrix (§4.1) exactly, including `_x` suffixes.
- **Format assertions**: `spread_sequence` matches `^\['.*'\]$` with `, ` separators (L5); counts are integers, not floats (L6); timestamps are ISO (L9).
- **Cross-file identity**: `subreddit` and `domain` value sets are consistent between `clean_posts.csv` and `subreddit_domain_flow_v2.csv` (L10).
- **Scaling guard**: `unique_authors` max stays under the threshold-count cap (L7).
- **Freshness**: `generated_at` within 48h, else fail loudly (L13).
- **Smoke**: after `patch_db.py`, hit one endpoint per dashboard and assert non-empty, non-mock payloads.

---

## 8. Failure handling

**Governing rule: a failed refresh serves yesterday's data, never broken data.**

| Failure | Behaviour |
|---|---|
| Reddit fetch fails | Abort before publish; `data-live` untouched; Actions fails loudly |
| One subreddit 403s | Proceed if ≥8/10 succeeded; log which were missed |
| Validation gate fails | No push. Checks: all 16 files present, non-empty, columns match, date range covers window, volume within sane bounds of trailing average |
| Workflow B (globe) fails | Workflow A unaffected; globe serves previous JSON; staleness surfaced |
| Workflow C (LLM) fails | **Non-blocking** — publish with TF-IDF fallback labels |
| Container download fails | Falls back to baked-in `/app/seed_data` |
| Groq rate limit | Exponential backoff; partial results published with a partial flag |

---

## 9. Rollout

Six phases, each independently shippable.

1. **ETL + golden-master tests** against existing data. No live fetch, no deploy changes. Proves correctness in isolation.
2. **Reddit source** wired in; run Workflow A manually and inspect output.
3. **Enable cron** + `entrypoint.sh` download + seed fallback. Core dashboards go live-daily.
4. **Warm-container refresh** + cache invalidation (L11, and `polarize_1` + the four module-level caches).
5. **Workflow B** — globe pipeline, after fixing L13 (staleness gate) and L14 (Nominatim).
6. **Workflow C** — LLM pre-generation: topic labels (kills the `LABEL_ME_2` placeholders currently rendering in the UI), domain categories (L16), executive brief, top-20 narrative analysis.

Then: **repo cleanup** — drop the four dead artifacts in §4.2 (~25 MB), keeping a small
frozen snapshot as the seed fallback.

Phases 1–3 deliver the core ask. 4–6 are polish.

---

## 10. Open items

**Resolved by the forensic pass** (see §6): `spread_strength`, echo-chamber lift, the
ideological-distance metric, `narrative_id` construction, `final_influence_score`
coefficients, and the dedupe mechanism are all now verified rather than open. The Reddit
listing question is also settled — 9 of 10 subreddits sit at ~1,000 posts in the original
dump, the listing pagination ceiling, so the `top?t=day` union is required, not optional.

Still genuinely open:

**~~1. The hardcoded domain→category dict.~~ DECIDED — grow it nightly, cached.**
`clean_top_distinctive_domains` restricted its universe to ~41 hand-categorized domains
(§6.4), which does not survive live data. Resolution:

```
new domain → in domain_categories.json?  ── yes → use cached category (free)
                                         └─ no  → one LLM call → append → never asked again
```

The cache persists on the `data-live` branch beside the artifacts, mirroring the existing
`ai_cache.json` pattern. Cost decays fast: a handful of new domains per day after the first
run, since the political web has a long tail but a small head. Well inside free-tier limits
(Groq allows 14,400 requests/day; the full nightly job needs ~180).

⚠️ **The seed must be the whole corpus, not the 41 domains CTDD exposes.** Measured during
implementation: seeding from those 41 and regenerating gives a universe of 1,107 posts
against the original 3,213, and the resulting echo-chamber scores do not merely drift —
**the ranking inverts.** Anarchism falls from 26.70 (most insular) to 8.20 while neoliberal
rises from 5.49 to 9.30. A rule-based blocklist does not rescue it either: excluding
`self.*`, image hosts, shorteners and link-farms yields 4,718 posts, overshooting by 47%
and unevenly (Anarchism 2.8× over target, politics 1.3×).

So Workflow C's first run must categorize **all ~909 domains in the corpus**, not extend
from 41 — roughly 909 one-time calls. Until that runs, `clean_top_distinctive_domains` and
`echo_chamber_scores` are the only two artifacts that will not match the deployed site.
The transform code is correct; the input is pending.

Two consequences to accept:
- `sub_total`'s denominator (currently the hardcoded `3213`) grows as the dict grows, so
  every subreddit's echo-chamber score shifts even when behaviour does not. This is
  correct — "distinctive versus everything observed" rather than "versus 41 sites chosen
  once" — but scores will not be comparable to the currently deployed values.
- A number that is deterministic today acquires an LLM dependency. Mitigate by treating
  categorization failures as non-blocking: an uncategorized domain falls back to `Other`
  and is retried the next night, never blocking the run.

Fix alongside: the frontend expects category names (`News, Blogs, Advocacy, Research,
Government`, `PolarizeTreemap.jsx:26-32`) that do not match the data's (`Media / News`,
`Advocacy / Org`). Pre-existing bug; the new categorizer should emit the frontend's
vocabulary.

**~~2. Gen-A artifacts.~~ DECIDED — regenerate in Gen-B format.** `narrative_registry`,
`narrative_diffusion_table`, `author_influence_profile`, `amplification_event_table`,
`user_intelligence_summary`, and `author_amplification_summary` came from an older run
using float ids with **zero** `narrative_id` overlap with Gen-B (§6.8), and
`author_amplification_summary` did not reconcile with its own source (0/65). All six are
regenerated from the same run as everything else. The `.replace(".0_", "_")` bridge (L4)
becomes dead code and should be removed along with them. Consequence: every `narrative_id`
the chatbot indexes changes, so `hybrid_vector_meta.jsonl` must be rebuilt in the same pass.

**~~3. The dedupe subset filter.~~ SOLVED — see §6.2.** The discriminator is **≥2 distinct
authors** per title group. Verified by perfect separation across all 243 candidate groups
and an identical post-id set against the committed artifact. Cluster numbering is
alphabetical by title (131/131). Nothing remains open here.

**2. `backend/database.py`.** Confirmed broken beyond repair-by-column-rename: it selects
`title`, `domains`, `top_subreddits`, `top_authors` from a CSV that has none of them, opens
the DB read-write, and creates *views* over names `patch_db.py` creates as *tables* —
DuckDB views **do** persist across connections (proven by the recovered DB), so a "fix"
would replace `narratives` with a view exposing `internal_system_id` and break all 12
`networkgraph` join sites. No module imports it. **Recommend deletion**, along with
`backend/hybrid/`, `backend/hybrid_crew/`, and the commented-out `main.py:1-453`.
