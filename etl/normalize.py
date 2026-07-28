"""Raw Reddit listing JSON → clean_posts (spec §6.1)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from etl.config import CLEAN_POSTS_COLUMNS, EXCLUDED_AUTHORS_CLEAN


def load_listing(path: Path) -> pd.DataFrame:
    """Read JSON Lines of {"kind": "t3", "data": {...}} into a flat frame.

    Only `t3` (link/post) entries are kept; other kinds (e.g. Reddit pagination
    objects) are skipped rather than surviving as all-NaN rows.

    Uses `max_level=0` so nested fields (e.g. `media_metadata`, keyed per-image)
    are not recursively flattened into thousands of extra columns — we only need
    the 10 columns in `CLEAN_POSTS_COLUMNS`.
    """
    with Path(path).open("r", encoding="utf-8") as fh:
        entries = (json.loads(line) for line in fh if line.strip())
        records = [entry["data"] for entry in entries if entry.get("kind") == "t3"]
    return pd.json_normalize(records, max_level=0)


def normalize_listing(path: Path) -> pd.DataFrame:
    """Produce the clean_posts contract: 10 columns, AutoModerator removed.

    `domain` is Reddit's server-computed field and is copied verbatim. Deriving it
    from `url` disagrees on 57% of rows, so never do that.
    """
    df = load_listing(path)
    df = df[~df["author"].isin(EXCLUDED_AUTHORS_CLEAN)].copy()
    df["created_datetime"] = pd.to_datetime(df["created_utc"], unit="s")
    # Reddit emits domain="" (not a missing key) for relative-URL crossposts.
    # The golden master went through a CSV round-trip, where pandas' default
    # NA handling turns that blank cell into np.nan on read; match that here
    # (np.nan, not pd.NA) so the in-memory frame is what CSV serialization
    # would actually produce.
    df["domain"] = df["domain"].replace("", np.nan)
    return df[CLEAN_POSTS_COLUMNS].reset_index(drop=True)
