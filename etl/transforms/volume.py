"""Daily post volume (spec §6.6)."""
import pandas as pd


def build_daily_volume(posts: pd.DataFrame) -> pd.DataFrame:
    """Calendar-day counts over the 8,567 set. Emits only days with >=1 post.

    created_datetime is emitted as an ISO date string ("YYYY-MM-DD"), matching
    the committed CSV contract. Grouping by `.dt.date` alone leaves the column
    holding `datetime.date` objects, which compare unequal to the CSV's plain
    strings under `pd.testing.assert_frame_equal` even though they render the
    same (`datetime.date(2024, 7, 23) == "2024-07-23"` is False).
    """
    dates = pd.to_datetime(posts["created_datetime"]).dt.strftime("%Y-%m-%d")
    counts = posts.groupby(dates).size()
    return (
        counts.rename("post_count")
        .rename_axis("created_datetime")
        .reset_index()
    )


def reindex_complete_days(volume: pd.DataFrame) -> pd.DataFrame:
    """Zero-fill missing calendar days.

    The spike detector at backend/streamgraph2/data/db.py:498-502 uses a *row-based*
    rolling window, so an absent day silently shortens the baseline. Over a 7-day
    live window one missing day is a 14% distortion.
    """
    idx = pd.to_datetime(volume["created_datetime"])
    full = pd.date_range(idx.min(), idx.max(), freq="D")
    out = (
        volume.assign(created_datetime=idx)
        .set_index("created_datetime")
        .reindex(full, fill_value=0)
        .rename_axis("created_datetime")
        .reset_index()
    )
    out["created_datetime"] = out["created_datetime"].dt.strftime("%Y-%m-%d")
    return out
