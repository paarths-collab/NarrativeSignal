# Live Data Layer — Design

**Date:** 2026-07-28
**Status:** Approved design, pending implementation plan
**Repo:** `paarths-collab/research-engineering-intern-assignment`

---

## 1. Problem

The dashboards present themselves as a narrative-intelligence platform but serve a
frozen snapshot: 8,711 Reddit posts from 2024-07-23 to 2025-02-18 across 10 political
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

**L1 — `.npy` positional alignment.** `title_embeddings_v2.npy` joins to
`clean_posts.csv` by **row position**, with no id join. The files are *already*
misaligned: 8,567 embeddings versus 8,711 rows. All three readers silently truncate to
`min(len)` (`cluster_pipeline.py:100-104`, `db.py:353-354`). Any insertion, re-sort, or
dedupe shifts every embedding onto the wrong post and mislabels all clusters with no
error. If regenerated, both files must be emitted together in identical row order.

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

**`transforms/dedupe.py`** → `clean_with_clusters_v2.csv`
Assigns `duplicate_cluster_id` — the same story crossposted across subreddits. Hash-embed
titles (`streamgraph2/models/embedder.py:18`, MD5 token hashing, 384-dim, no model
download), block by domain first for O(n) per group, cosine-threshold, union-find. A
*narrative* is a duplicate cluster.

**`transforms/narratives.py`** → 4 artifacts
`narrative_intelligence_summary` (`narrative_id = f"{cluster_id}_{primary_domain}"`,
representative title = highest-scoring post), `narrative_topic_mapping`,
`narrative_spread_chain_table` (subreddits ordered by first appearance),
`graph_edge_intelligence_table` (one row per post, with `origin_subreddit` and
`hours_from_origin`).

**`transforms/ecosystem.py`** → 3 artifacts
`subreddit_domain_flow_v2` (groupby count), `clean_top_distinctive_domains`
(`lift = p(domain|sub) / p(domain)` — the formula is spelled out by the CSV's own
columns), `echo_chamber_scores` (per-subreddit aggregate lift).

**`transforms/authors.py`** → 3 artifacts
`bridge_authors_v2` (authors in ≥2 subreddits), `author_amplification_summary` (score
relative to subreddit baseline), `author_influence_profile`.

**`transforms/volume.py`** → `daily_volume_v2.csv`
Post counts keyed by `created_datetime` date. Emits one row per calendar day in the
window — the rolling window at `db.py:498-502` is row-based, not calendar-based, so
missing days silently compress the baseline and inflate z-scores. An hourly series ships
as a *new* file rather than a schema change to this one.

**`transforms/chatbot.py`** → `narrative_registry`, `narrative_diffusion_table`,
`subreddit_intelligence_summary`.

Coverage check: `clean_posts.csv` from `normalize.py`, plus 15 derived across the five
transform modules = all 16 required CSVs.

`spread_strength` and the echo-chamber lift aggregation are not fully determined by
column names and require reverse-engineering — validated by the golden-master test.

---

## 7. Testing

### Golden-master test — the primary correctness mechanism

Run the new ETL over the committed `clean_posts.csv` and assert it reproduces the
committed derived CSVs. 8,711 real posts with known-correct outputs already sit in git.

This does three jobs: proves the reverse-engineered formulas correct, guards every future
regression, and lets the entire ETL be built and validated **before writing any
Reddit-fetching code**.

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

- `spread_strength` and echo-chamber lift formulas to be pinned by golden-master.
- Reddit listing strategy per subreddit: `new` paginated versus `top?t=day` union. `new` is chronologically complete but caps at ~1000; the union covers high-volume subreddits like r/politics.
- Whether to fix L12 (`backend/database.py` view/table conflict) or disable that code path outright.
- `narrative_registry` id format: normalize to `0_` and fix the silent lookup miss at `vector_store.py:222-256`, or preserve `0.0_` for compatibility.
