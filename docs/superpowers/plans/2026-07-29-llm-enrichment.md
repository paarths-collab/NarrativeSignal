# Phase 6: LLM Enrichment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the two LLM-blocked artifacts into fresh ones. `domain_categories.json` gets
extended nightly with any new domains; `narrative_topic_mapping.csv`'s `LABEL_ME_*`
placeholders get replaced with real cluster labels. Two dashboards that currently drift or
render "LABEL_ME_2" become correct.

**Architecture:** A separate Actions job (`enrich`) that runs after `nightly-etl` succeeds.
Reads `uncategorized_domains.txt` + `narrative_topic_mapping.csv` from `data-live`, calls
Groq (Gemini failover on 429/5xx), writes updated `domain_categories.json` +
`narrative_topic_mapping.csv`, re-publishes to `data-live`. Cache-first: a domain already
in the JSON is never re-asked.

**Tech stack:** Groq + Gemini via existing REST clients. No new runtime deps in
`requirements.txt` — enrichment runs only in Actions, never in the container.

**Scope note:** Executive brief and top-20 narrative summaries mentioned in spec §9 are
NOT in this phase — they're bigger and can wait. This phase covers the two artifacts that
today ship visibly-wrong content (category-space drift + `LABEL_ME_*`).

---

### Task 1: `enrich/` package scaffolding + LLM client

**Files:** Create `enrich/__init__.py`, `enrich/llm.py`, `tests/enrich/__init__.py`,
`tests/enrich/conftest.py`, `tests/enrich/test_llm.py`.

`enrich/llm.py` exposes `classify(prompt, choices) -> str` — a thin wrapper that:
- calls Groq first, tries Gemini on `429 | 5xx`
- retries with tenacity, backoff 1s → 8s, 4 attempts
- constrains output to `choices` (rejects and retries if the LLM returns anything else)
- raises `ClassifyError` after exhausting retries — caller decides what to do

- [ ] TDD: `test_classify_returns_a_choice`, `test_falls_back_to_gemini_on_groq_429`,
  `test_rejects_out_of_vocab_response_and_retries`, `test_raises_after_exhaust`.
  Mock the HTTP layer; no real API calls in tests.

- [ ] Verify with `./.venv-etl/Scripts/python.exe -m pytest tests/enrich -q -W error`

- [ ] Commit `feat(enrich): LLM classifier with Groq/Gemini failover`

---

### Task 2: `enrich/categorize_domains.py`

**Files:** Create `enrich/categorize_domains.py`, `tests/enrich/test_categorize.py`.

Reads `data/domain_categories.json` + `uncategorized_domains.txt`, calls
`classify()` for each new domain with the vocab
`["News", "Blogs", "Advocacy", "Research", "Government", "Video", "Other"]` (matches
`frontend/app/polar/PolarizeTreemap.jsx:26-32` so the frontend doesn't need to change),
writes the merged JSON back.

**Prompt template:**
```
Given the news/media domain "{domain}", classify it as ONE of:
News, Blogs, Advocacy, Research, Government, Video, Other.

Answer with just the single word. If unsure between News and Blogs, prefer
News for established outlets and Blogs for personal/opinion sites.
```

**Cost control:** the CategoryStore.pending worklist on run 1 is ~868 domains, ~4s per
call in the plan's earlier measurement = 58 min sequential. Batch in groups of 8 concurrent
requests, cap at 900 categorization calls per invocation, log the leftovers. Groq free tier
is 14,400 req/day so this is safely inside budget on run 1 and near-zero afterward.

- [ ] TDD: golden test with a fake `classify()` that returns preset answers, verifies the
  JSON merge, verifies `pending` shrinks, verifies rate-limit caps

- [ ] Verify, commit `feat(enrich): domain categorizer with cache-first merge`

---

### Task 3: `enrich/label_topics.py`

**Files:** Create `enrich/label_topics.py`, `tests/enrich/test_label_topics.py`.

Reads `narrative_topic_mapping.csv` + `narrative_intelligence_summary.csv` (for the
`representative_title` per narrative). For each of the 5 topic clusters, calls the LLM
once with the top 20 representative titles in that cluster and asks for a 2-4 word label.
Writes the CSV back with `topic_label` filled in.

**Prompt template:**
```
Below are 20 headlines from Reddit posts that cluster together as one topic.
In 2-4 words, name the shared subject of these headlines.

{titles as a bulleted list}

Answer with just the label — no quotes, no punctuation, no explanation.
```

Only 5 calls per run, so no batching needed.

- [ ] TDD: with a fake `classify()` returning `"US Politics"` etc., verify the CSV rewrites
  correctly, verify no `LABEL_ME_` strings remain, verify column contract preserved

- [ ] Verify, commit `feat(enrich): topic label generator, 5 calls per run`

---

### Task 4: `enrich` CLI

**Files:** Create `enrich/__main__.py`, `enrich/cli.py`, `tests/enrich/test_cli.py`.

```
python -m enrich categorize --in data/ --out data/
python -m enrich label       --in data/ --out data/
python -m enrich all         --in data/ --out data/
```

- [ ] Standard argparse pattern matching `fetcher/cli.py`. Verify, commit.

---

### Task 5: Add an `enrich` job to `nightly-etl.yml`

**Files:** Modify `.github/workflows/nightly-etl.yml`. Add test.

The job:
- `needs: nightly` (only runs on success)
- checks out `data-live` (not `live-data-layer` — we mutate published artifacts)
- runs `python -m enrich all --in . --out .`
- re-publishes with `bash scripts/publish_to_data_live.sh . data-live`
- triggers Render redeploy hook if set

Gated on `GROQ_API_KEY` being present. If not set, the job is skipped and the topic labels
stay `LABEL_ME_*` — degraded but not broken.

- [ ] TDD in `tests/refresh/test_workflow_yaml.py`: `enrich` job exists, needs nightly,
  runs after publish, gated on secret.

- [ ] Verify workflow YAML parses, commit.

---

### Task 6: Documentation

**Files:** Modify `docs/daily-refresh-runbook.md`. Update `docs/deploy-secrets.md` (the
`GROQ_API_KEY` section already exists — clarify what it enables).

- [ ] Add an "Enrichment" section to the runbook: what runs, how to verify (check that
  `LABEL_ME_*` no longer appears in `narrative_topic_mapping.csv` on `data-live`, and that
  `domain_categories.json` grew).

- [ ] Note the first-night cost: ~868 categorization calls + 5 label calls ≈ 15 minutes at
  8-way concurrency. Subsequent nights: usually <20 domains + 5 labels ≈ 30 seconds.

- [ ] Commit.

---

## Definition of Done

- [ ] `python -m enrich all` runs end-to-end on committed data with mocked LLM
- [ ] `domain_categories.json` grows monotonically; a domain is never re-asked
- [ ] `narrative_topic_mapping.csv` contains real labels, no `LABEL_ME_`
- [ ] Nightly workflow runs enrichment after publish and re-publishes
- [ ] Gated on `GROQ_API_KEY`; skips cleanly if absent
- [ ] Full test suite still green
