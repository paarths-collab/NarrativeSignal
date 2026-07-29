#!/bin/sh
# Container startup: fetch fresh artifacts from data-live, then start the app.
#
# The download is best-effort. On any failure the container falls back to the
# baked-in /app/seed_data (which is a copy of data/ from the image build).
# This means a first boot never has zero data.
#
# Environment:
#   DATA_PATH             persistent data mount   (default /app/data)
#   SEED_PATH             in-image fallback dir   (default /app/seed_data)
#   FETCH_DATA_LIVE_URL   tarball URL             (skip download if unset)
#   FETCH_DATA_LIVE_SCRIPT path to helper         (default /app/scripts/fetch_data_live.sh)
#   SKIP_UVICORN          exit before uvicorn     (test hook only, must be unset in prod)

set -eu

DATA_PATH="${DATA_PATH:-/app/data}"
SEED_PATH="${SEED_PATH:-/app/seed_data}"
FETCH_SCRIPT="${FETCH_DATA_LIVE_SCRIPT:-/app/scripts/fetch_data_live.sh}"

mkdir -p "$DATA_PATH"

if [ -n "${FETCH_DATA_LIVE_URL:-}" ] && [ -x "$FETCH_SCRIPT" ]; then
  echo "[entrypoint] Fetching data-live from $FETCH_DATA_LIVE_URL"
  # fetch_data_live.sh handles its own fallback to seed on download failure
  "$FETCH_SCRIPT" "$FETCH_DATA_LIVE_URL" "$DATA_PATH" "$SEED_PATH" || {
    echo "[entrypoint] fetch script failed; using existing $DATA_PATH" >&2
  }
elif [ ! -f "$DATA_PATH/clean_posts.csv" ] && [ -d "$SEED_PATH" ]; then
  echo "[entrypoint] No FETCH_DATA_LIVE_URL and no data present; seeding from $SEED_PATH"
  cp -a "$SEED_PATH"/. "$DATA_PATH"/
fi

# Invalidate any cache that references the old artifact set
rm -f "$DATA_PATH/posts_clustered.parquet" 2>/dev/null || true

echo "[entrypoint] Patching DuckDB intelligence views..."
python patch_db.py

if [ "${SKIP_UVICORN:-}" = "1" ]; then
  echo "[entrypoint] SKIP_UVICORN=1; boot verification complete"
  exit 0
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
