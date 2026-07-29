# Fetcher First Run

Before the first cron ever fires, prove locally that the fetcher can reach Reddit.

## 1. Create a Reddit "script" app

https://www.reddit.com/prefs/apps → **create app** → **script**. Note the client id
(under the app name) and secret. No redirect URI is used.

## 2. Set env vars in your shell

```bash
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="narrativesignal/1.0 by u/<your-username> (contact: you@example.com)"
```

The User-Agent format matters — a bare `python:asyncpraw` will be rate-limited hard, and
the fetcher refuses to run with it.

## 3. Run against yesterday

```bash
mkdir -p fetcher_out
./.venv-etl/Scripts/python.exe -m fetcher fetch --out fetcher_out/day.jsonl
```

Expected output:
```
wrote NNN records to fetcher_out/day.jsonl
```

Where NNN is roughly 30–3000 depending on the day. `fetcher_out/` is gitignored.

## 4. Feed through the ETL

```bash
./.venv-etl/Scripts/python.exe -c "
from pathlib import Path
from etl.normalize import normalize_listing
from etl.pipeline import run_pipeline, REQUIRED_ARTIFACTS

clean = normalize_listing(Path('fetcher_out/day.jsonl'))
run_pipeline(clean, Path('fetcher_out/artifacts'),
             categories_path=Path('data/domain_categories.json'))
missing = [n for n in REQUIRED_ARTIFACTS if not (Path('fetcher_out/artifacts')/n).exists()]
print('missing:', missing or 'none')
"
```

The last line must print `missing: none`. If any artifact is missing, the fetcher or
normalize failed for that day — check the log for `skipping r/...` and rerun.

## 5. If Reddit returns 429

The User-Agent is being ignored (a generic UA), or you hit the rate limit while iterating.
Wait five minutes and try again. If it persists, the client id/secret is wrong.

## CI story

This doc covers a local, manual smoke run only. The nightly GitHub Actions workflow, its
secrets, and the `data-live` publish step are a separate concern — see
`docs/superpowers/plans/2026-07-29-daily-refresh.md` Task 8 (`docs/deploy-secrets.md`) for
how the same four env vars get set as repository secrets for CI.
