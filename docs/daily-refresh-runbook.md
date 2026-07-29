# Daily Refresh Runbook

## First-run checklist

Before running the workflow for the first time, complete `docs/deploy-secrets.md`.

## Trigger a manual run

```
gh workflow run nightly-etl --repo paarths-collab/NarrativeSignal \
  --ref live-data-layer -f date=2025-02-17
```

Watch it in the Actions tab. Expected duration: **2–4 minutes**.

Success looks like:
1. **Fetch**: `wrote NNN records to out/day.jsonl` where NNN > 0
2. **Run ETL**: no traceback, `artifacts/` gets 19 CSV files
3. **Validate artifacts**: `OK: 19 artifacts present, non-empty`
4. **Publish to data-live**: `pushed artifacts to origin/data-live`

If any step fails, check that step's log — the messages are self-explanatory.

## Verify data-live got updated

```
git fetch narrativesignal data-live
git log narrativesignal/data-live --oneline -1
```

Should show a commit dated in the last few minutes with message
`nightly artifacts <timestamp>`.

## Verify the container redeployed (if RENDER_DEPLOY_HOOK is set)

The workflow's final step logs `Trigger Render redeploy` — expect a 200 OK JSON response.
Then on Render → your service → **Events**, a new deploy should appear within a minute of
the workflow completing. Once its status flips to `Live`, hit `/health` on the API — the
first response after restart will include the freshest data.

If the step logs `RENDER_DEPLOY_HOOK not set; skipping redeploy trigger`, that's the
optional path. Redeploy Render manually to pick up the new artifacts.

## Verify the container picks it up

Redeploy on Render (or wait for the next natural restart). Watch the logs; expect:

```
[entrypoint] Fetching data-live from ...
[fetch_data_live] downloaded and extracted to /app/data
[entrypoint] Patching DuckDB intelligence views...
[patch] Imported: [...]
```

If the fetch fails, you'll see the fallback line and the container will still boot on
seed data — the site stays up on yesterday's numbers rather than going dark.

## What if the cron missed a night?

`workflow_dispatch` accepts a `date` input. Rerun for the missed day; only the last
push wins on `data-live`, which is fine — we care about the most recent day.

## What if the site is showing wrong data?

1. Check `data-live` timestamp (above).
2. If stale, run the workflow manually.
3. If fresh but wrong, check `/health` on the API — the container may be running on
   the fallback seed because the tarball download failed silently. Redeploy.
4. If still wrong, check the smoke tests in `tests/etl/` — they may reveal a
   regression the CI didn't catch.

---

## Enrichment (LLM categorization + topic labels)

The nightly workflow's `enrich` job runs after `nightly` succeeds if `GROQ_API_KEY` is set.
It reads the current `data-live`, categorizes any new domains, replaces
`LABEL_ME_*` topic labels with real ones, and re-publishes.

**First-night cost:** ~868 categorization calls + 5 label calls ≈ 15 minutes at 8-way
concurrency. Well inside Groq's free-tier ceiling (14,400 requests/day).

**Subsequent nights:** usually <20 new domains + 5 labels ≈ 30 seconds.

### Verification

- `git fetch narrativesignal data-live && git show narrativesignal/data-live:narrative_topic_mapping.csv | head`
  — should show real labels, no `LABEL_ME_`
- `git show narrativesignal/data-live:domain_categories.json | jq 'length'`
  — should be >41 after the first successful run
- `uncategorized_domains.txt` should have shrunk (leftover domains only if the cap was hit)

### Troubleshooting

- If topics still show `LABEL_ME_`: `enrich` job likely skipped because `GROQ_API_KEY`
  is unset — check the workflow logs.
- If categorization stalled: Groq rate-limited. Cap defaults to 900/run; the leftovers
  land in the next night's `uncategorized_domains.txt` and get processed then.
- If the LLM returned wrong categories: `_reseed_defaults` maps the legacy vocab
  once, but the LLM's classification for a specific domain is committed to
  `domain_categories.json` and won't re-ask. To reclassify a domain, delete its
  entry from the JSON and re-run.

---

## Globe pipeline (Layer 5 of the nightly)

Runs as a step in the `nightly` job (not a separate job) so both the 19 CSVs and
`global_events_latest.json` land in a single commit. The globe step is `continue-on-error:
true` — a globe failure NEVER blocks the ETL publish.

### First-run expectations

- Duration: ~15-25 minutes (140 Groq calls + Nominatim geocoding)
- Output: 15-30 events in `global_events_latest.json`, mix of continents
- **Don't cancel early.** The first Actions run with globe enabled will be ~25 min
  longer than the current baseline; that's normal, not a hang.

### Local smoke test

```bash
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT='...'
export GROQ_API_KEY=...
export GLOBE_NOMINATIM_UA='narrativesignal-globe/1.0 (contact: <your-email>)'
cd backend/globe
python run_full_pipeline.py
ls -la outputs/  # global_events_YYYY-MM-DD.json should appear
```

### Verification

- Check the Actions log for the "Run globe pipeline" step; expect to see Layer 1..Layer 9
  progression, followed by "Pipeline Run ID: XXX" and a status of `complete`.
- `git fetch narrativesignal data-live && git show narrativesignal/data-live:global_events_latest.json | jq '.event_count'`
  — expect a positive integer.
- If it says `{"events": [], "reason": "no data available"}` — the globe step failed but
  the validator (correctly) let the ETL publish. Check the Actions logs.

### Troubleshooting

- **"GLOBE_NOMINATIM_UA looks generic"** — set the env var to a real
  `product/version (contact: your@email)` form, not `simppl-globe/1.0`.
- **Nominatim 429s** — respect the 1-req/sec policy; check that `geocoder.py`'s
  rate limiter is intact.
- **Groq 429s** — the pipeline's LLM calls are already retried via `enrich.llm`. If
  you see them in the log but the run succeeds, that's expected behavior.
