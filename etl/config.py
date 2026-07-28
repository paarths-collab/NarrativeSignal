"""Shared paths and column contracts for the ETL."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

SUBREDDITS = [
    "Anarchism", "Conservative", "Liberal", "PoliticalDiscussion", "Republican",
    "democrats", "neoliberal", "politics", "socialism", "worldpolitics",
]

# Exact column order of clean_posts.csv. Order is part of the contract.
CLEAN_POSTS_COLUMNS = [
    "id", "created_datetime", "subreddit", "author", "title",
    "domain", "score", "num_comments", "permalink", "url",
]

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Row-count cascade filters (spec §6.0)
EXCLUDED_AUTHORS_CLEAN = ["AutoModerator"]
EXCLUDED_AUTHORS_POSTS = ["[deleted]"]
