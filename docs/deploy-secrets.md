# Deploy secrets

Four GitHub Actions secrets must be set on `paarths-collab/NarrativeSignal` before
the nightly refresh workflow can succeed. This is a one-time, ~10-minute task and
is the ONLY manual step in the entire live-data-layer rollout. (A fifth,
`GLOBE_NOMINATIM_UA`, gates the optional globe run — see the note at the end of
step 3.)

## 1. Reddit app

1. https://www.reddit.com/prefs/apps → **are you a developer? create an app**
2. Type: **script**
3. Name: `narrativesignal`
4. Redirect URI: `http://localhost:8080` (unused; must be non-empty)
5. Note the client id (14 chars under the app name) and secret

## 2. Groq key

1. https://console.groq.com → API Keys → **Create API Key**
2. Copy immediately; it is shown only once

**What this unlocks:** `GROQ_API_KEY` gates the nightly `enrich` job (see
`docs/daily-refresh-runbook.md` → Enrichment). Without it, the site still works end
to end, but two artifacts stay visibly degraded: `narrative_topic_mapping.csv` keeps
its `LABEL_ME_2`-style placeholder labels instead of real cluster names, and
`domain_categories.json` stops growing, so newly-seen domains drift into whatever
default bucket the legacy vocab maps them to on the polarization dashboard. With it
set, `enrich` runs after every successful `nightly` job, categorizes new domains,
replaces the `LABEL_ME_*` placeholders with real labels, and re-publishes — both
artifacts carry fresh, correct content each morning.

## 3. Set the four GitHub secrets

https://github.com/paarths-collab/NarrativeSignal/settings/secrets/actions →
**New repository secret** for each:

| Name | Value |
|---|---|
| `REDDIT_CLIENT_ID` | from step 1 |
| `REDDIT_CLIENT_SECRET` | from step 1 |
| `REDDIT_USER_AGENT` | `narrativesignal/1.0 by u/<your-reddit-username> (contact: <your-email>)` |
| `GROQ_API_KEY` | from step 2 |
| `GLOBE_NOMINATIM_UA` (optional) | `narrativesignal-globe/1.0 by u/<your-reddit-username> (contact: <your-email>)` |

The User-Agent format matters. Reddit rate-limits generic UAs, and the fetcher
refuses to run with a placeholder value — `load_user_agent()` in `fetcher/config.py`
rejects any value containing `python:asyncpraw`, `changeme`, or `default`
(case-insensitive) with a `RuntimeError` before a single request is made. Use the
real format above, with your actual Reddit username and contact email filled in —
not a copy-pasted generic string.

The globe pipeline's geocoding step needs the same treatment: set
`GLOBE_NOMINATIM_UA` to `narrativesignal-globe/1.0 by u/<your-reddit-username>
(contact: <your-email>)`. Nominatim's usage policy requires a contactable
User-Agent on every request; `get_nominatim_user_agent()` in
`backend/globe/app/pipeline/geo_resolver.py` shares the exact same generic-value
guard (via `fetcher.config._load_ua_from_env`) and raises before Layer 3 (geo
resolution) makes any Nominatim calls if the value is missing or generic. This
secret is optional in the sense that its absence doesn't break the nightly ETL —
but it does gate the globe run (see `docs/superpowers/plans/2026-07-29-globe-pipeline.md`
Task 4), which is skipped cleanly if either `GROQ_API_KEY` or `GLOBE_NOMINATIM_UA`
is unset. Without it, the globe step is skipped cleanly and the frontend shows the
previous day's `global_events_latest.json` (or the empty-with-`reason` placeholder from
Task 1 if no prior file exists) — see `docs/daily-refresh-runbook.md`'s "Globe pipeline"
section for verification steps.

## 4. Initialize the data-live branch (once)

The nightly workflow only ever force-pushes to an *existing* `data-live` branch —
it never creates one from scratch. Before the first run, create it yourself:

```
git checkout --orphan data-live
git rm -rf .
echo "placeholder for artifacts" > README.md
git add README.md
git -c user.email=you@you.com -c user.name=you commit -m "init data-live"
git push narrativesignal data-live
git checkout live-data-layer
```

(`narrativesignal` is the remote pointing at `paarths-collab/NarrativeSignal`; use
whatever remote name you have configured for that repo.)

After this, `scripts/publish_to_data_live.sh` will keep overwriting `data-live`
with a fresh single-commit orphan history every night — you never touch it again.

## 5. Verify with the workflow

```
gh workflow run nightly-etl --repo paarths-collab/NarrativeSignal \
  --ref live-data-layer -f date=2025-02-17
```

Then follow `docs/daily-refresh-runbook.md` to confirm the run succeeded, `data-live`
was updated, and a container reboot picks up the new artifacts.

## 6. Optional: Render deploy hook

Without this, `data-live` will be updated nightly but your container will only see the new
data on its next natural restart. Setting this triggers an immediate redeploy after each
successful publish — ~30 seconds of downtime at 03:15 UTC, and your site is fresh.

1. Render dashboard → your service → **Settings** → **Deploy Hook**
2. Copy the URL (it embeds a per-service token; treat it like a credential)
3. GitHub → **Settings → Secrets → Actions → New repository secret**:
   - Name: `RENDER_DEPLOY_HOOK`
   - Value: the URL from step 2

If you skip this, everything else still works — you just get a stale site until the
container is bounced.
