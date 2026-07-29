# Phase 5: Globe Pipeline Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The globe page shows fresh geopolitical events every morning, from real data,
not the 6 hardcoded mocks (Tel Aviv/Tehran/Kyiv) it falls back to today.

**Architecture:** The globe backend already exists — `backend/globe/` has a 10-stage async
orchestrator (`run_pipeline()`) that fetches Reddit, resolves locations, correlates news,
scores impact, and writes `global_events_<date>.json`. This phase wires it into the daily
Actions run, publishes its output to `data-live`, and fixes two known landmines that would
make live data unshippable (L13 mock fallback, L14 Nominatim policy violation).

**Tech stack:** Python 3.11 (already used), Groq (already wired), Nominatim (already
wired), plus one workflow step and two small backend fixes. No new runtime deps.

**Scope note:** This phase does NOT refactor the globe orchestrator to consume our
fetcher's output — Layer 1 fetches Reddit independently, which is duplicate work but
correct. A future phase can dedupe. **Not in scope:** re-writing any pipeline stage,
changing the JSON schema (the frontend depends on it), adding new event categories.

---

### Task 1: Kill the mock-events fallback (L13)

**Files:** `backend/globe/app/api/routes/events.py` — locate and remove the block that
returns 6 hardcoded Tel Aviv / Tehran / Kyiv events when `global_events_<date>.json` is
missing. Return `{"events": [], "reason": "no data available"}` instead. Test.

The mock fallback is the reason nobody noticed the pipeline stopped running — it renders
convincingly fake data. Removing it forces the failure to surface visibly.

- [ ] Locate: `grep -n "Tel Aviv\|Tehran\|Kyiv\|MOCK\|mock" backend/globe/app/api/routes/events.py`.
- [ ] Test: hit the events endpoint against a tmp dir with no JSON present → assert empty
  events list + non-500 response.
- [ ] Remove the block, verify, commit `fix(globe): remove hardcoded mock events fallback`.

---

### Task 2: Fix the Nominatim User-Agent (L14)

**Files:** `backend/globe/app/pipeline/geo_resolver.py`.

Nominatim's usage policy requires a User-Agent with a contact address. The current code
sends `narrativesignal-globe` or similar generic string, which risks blocking. Read the
value from `GLOBE_NOMINATIM_UA` env var, default to the same format we already validate
in `fetcher/config.py`: `narrativesignal-globe/1.0 (contact: <email>)`. Refuse to run if
the value is generic (matches the same denylist as `fetcher.config.load_user_agent`).

- [ ] Add the same guard function pattern — factor `load_user_agent(env_name, product)`
  into `etl/config.py` (or a new `common/user_agent.py`) since fetcher already has one.
- [ ] Wire it into geo_resolver.py.
- [ ] Add `GLOBE_NOMINATIM_UA` to `.env.example` and `docs/deploy-secrets.md`.
- [ ] Test that the guard rejects generic UAs.
- [ ] Commit `fix(globe): require Nominatim User-Agent with contact address`.

---

### Task 3: Smoke test the orchestrator in isolation

**Files:** `tests/globe/__init__.py`, `tests/globe/test_orchestrator_shape.py`.

Not a golden-master test — the orchestrator makes ~140 Groq calls and hits Nominatim, so
running it in CI is out. Instead: mock Layer 1 to return a small canned RawPost list, mock
the LLM and geocoder clients, and assert the orchestrator runs end-to-end without raising
and produces a JSON matching the schema the frontend expects (§4.3 of the design spec).

- [ ] Read `backend/globe/app/database/models.py` to see the actual `MapEvent` schema.
- [ ] Read `backend/globe/app/pipeline/orchestrator.py` to see which functions are
  imported — those are the mocking targets.
- [ ] Build a small canned `RawPost` set (3-5 posts across 2 subreddits, at least one
  with obvious geo content).
- [ ] Mock: `fetch_reddit_posts`, the LLM client's chat method, the geocoder's
  `resolve` call.
- [ ] Assert output JSON has `run_id`, `generated_at`, `event_count`, `events[]` and each
  event has the schema-required fields per spec §4.3 (`title`, `locations` with lat/lon,
  `timestamp`, `impact_score`, `sentiment`, `risk_level`, etc.).
- [ ] Commit `test(globe): shape test for the 10-layer orchestrator`.

---

### Task 4: Add the globe run to `nightly-etl.yml`

**Files:** Modify `.github/workflows/nightly-etl.yml`. Add a test.

New job `globe` runs after `nightly` succeeds (`needs: nightly`), on `ubuntu-latest`, with
a 30-minute timeout (~25 min real time observed for 140 Groq calls plus geocoding).

Steps:
1. checkout `live-data-layer`
2. pip install requirements
3. `cd backend/globe && python run_full_pipeline.py` — writes to
   `backend/globe/outputs/global_events_<date>.json`
4. copy the output to a tmp `artifacts/` dir
5. checkout `data-live` into a worktree, add the JSON, force-push (reuse the pattern from
   `publish_to_data_live.sh` but for a single file merge, not a replacement)

**Gated on `GROQ_API_KEY` AND `GLOBE_NOMINATIM_UA` both being set.** If either is missing,
skip cleanly — the globe page falls back to the previous day's JSON, and the "no events"
message from Task 1 shows if that's stale.

**⚠️ The publish step needs a merge-append, not a replace.** The existing
`publish_to_data_live.sh` orphan-force-pushes, which would wipe the 19 CSVs. Options:
(a) create a sibling `publish_to_data_live_append.sh` that fetches `data-live`, adds the
new file, force-pushes (still one commit total per push);
(b) run the globe job as part of `nightly` so both sets of artifacts land in one push.

**Pick (b)** — simpler, one commit per night, no chance of a partial state where the CSVs
updated but the globe didn't. The globe run then extends `nightly`'s timeout to 45 min.

- [ ] TDD in `tests/refresh/test_workflow_yaml.py`: assert a globe-run step exists, runs
  after ETL, before validate + publish; assert its output ends up in `artifacts/`.
- [ ] Verify workflow parses, commit.

---

### Task 5: Extend `REQUIRED_ARTIFACTS` and the validator

**Files:** Modify `etl/pipeline.py`, `scripts/validate_artifacts.py`, tests.

The globe writes `global_events_<date>.json`, filename varies daily. Options:
(a) always emit as `global_events_latest.json` for a stable name;
(b) emit both, keep `<date>` for provenance, `latest` as the pointer.

**Pick (b).** The frontend already reads `global_events_<date>.json` — check it, and
either update the frontend to read `latest` (simpler) or have `latest` be a symlink /
duplicate of the freshest.

- [ ] Read `frontend/app/globe/page.jsx` to see how the JSON filename is resolved.
- [ ] Update as needed.
- [ ] Add `global_events_latest.json` to `REQUIRED_ARTIFACTS`; extend `validate_artifacts`
  to check it's parseable JSON and has `event_count > 0` (or is a placeholder marked
  `"reason"` — see task 1's empty response format).
- [ ] Tests for both.
- [ ] Commit.

---

### Task 6: Verify with a manual dry run

**Files:** Add a section to `docs/daily-refresh-runbook.md`.

- [ ] Document how to run the globe orchestrator locally with real creds for a smoke test.
- [ ] Document what the first successful nightly looks like: expect ~15-25 events in the
  JSON, a mix of continents.
- [ ] Note: the first Actions run with globe enabled will be ~20 min longer than the
  current baseline; don't cancel it early.
- [ ] Commit.

---

## Definition of Done

- [ ] No more Tel Aviv/Tehran/Kyiv mocks in the served response
- [ ] Nominatim requests carry a contactable User-Agent
- [ ] Orchestrator shape test passes with mocked LLM + geocoder
- [ ] Nightly workflow produces `global_events_<date>.json` + `_latest.json` and pushes
- [ ] Frontend reads the fresh file without changes
- [ ] Full test suite still green

## Not in scope for Phase 5

- Deduping Layer 1 with our new `fetcher/` — the globe pipeline calls Reddit itself
- Fixing the frontend's `.filter(e => Number.isFinite(e.lat))` pin-drop logic
- Adding new event categories or geocoding sources
