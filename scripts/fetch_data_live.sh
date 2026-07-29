#!/usr/bin/env bash
# fetch_data_live.sh <tarball_url> <dest_dir> <seed_dir>
# Try to download and extract; on any failure copy seed_dir -> dest_dir.
# Fails only if seed is also unavailable.

set -uo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <tarball_url> <dest_dir> <seed_dir>" >&2
  exit 2
fi

URL="$1"
DEST="$2"
SEED="$3"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$DEST"

if curl -fsSL --max-time 30 "$URL" -o "$STAGE/artifacts.tar.gz"; then
  if tar -xzf "$STAGE/artifacts.tar.gz" -C "$STAGE" --strip-components=1; then
    # Copy the artifact files (not any nested dirs) into DEST atomically per-file.
    cp -a "$STAGE"/*.csv "$STAGE"/*.txt "$DEST"/ 2>/dev/null || true
    if [ -f "$DEST/clean_posts.csv" ]; then
      echo "[fetch_data_live] downloaded and extracted to $DEST"
      exit 0
    fi
  fi
  echo "[fetch_data_live] download extracted but incomplete" >&2
fi

echo "[fetch_data_live] fallback to seed $SEED" >&2
if [ -d "$SEED" ]; then
  cp -a "$SEED"/. "$DEST"/
  echo "[fetch_data_live] seeded from $SEED"
  exit 0
fi

echo "[fetch_data_live] FATAL: no data-live tarball and no seed at $SEED" >&2
exit 1
