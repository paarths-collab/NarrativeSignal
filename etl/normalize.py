"""Raw Reddit listing JSON → clean_posts (spec §6.1)."""
import json
from pathlib import Path

import pandas as pd

from etl.config import CLEAN_POSTS_COLUMNS, EXCLUDED_AUTHORS_CLEAN


def load_listing(path: Path) -> pd.DataFrame:
    """Read JSON Lines of {"kind": "t3", "data": {...}} into a flat frame."""
    with Path(path).open("r", encoding="utf-8") as fh:
        records = [json.loads(line)["data"] for line in fh if line.strip()]
    return pd.json_normalize(records)


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
    # NA handling turns that blank cell into NaN; match that here so the
    # in-memory frame is equivalent to what CSV serialization would produce.
    df["domain"] = df["domain"].replace("", pd.NA)
    return df[CLEAN_POSTS_COLUMNS].reset_index(drop=True)
