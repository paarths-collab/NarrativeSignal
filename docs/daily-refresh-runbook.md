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
