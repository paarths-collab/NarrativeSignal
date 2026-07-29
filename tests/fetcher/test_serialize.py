import io
import json

import pytest

from fetcher.serialize import to_jsonl_bytes, write_jsonl


@pytest.fixture()
def raw_records():
    return [
        {"id": "abc", "author": "u1", "domain": "self.politics",
         "created_utc": 1739858460.0, "subreddit": "politics"},
        {"id": "def", "author": "u2", "domain": "nytimes.com",
         "created_utc": 1739858461.0, "subreddit": "politics"},
    ]


def test_shape_is_kind_t3_wrapping_data(raw_records):
    lines = to_jsonl_bytes(raw_records).decode("utf-8").splitlines()
    assert len(lines) == 2
    for line, raw in zip(lines, raw_records):
        obj = json.loads(line)
        assert obj["kind"] == "t3"
        assert obj["data"] == raw


def test_line_endings_are_lf(raw_records):
    payload = to_jsonl_bytes(raw_records)
    assert b"\r\n" not in payload
    assert payload.endswith(b"\n")


def test_normalize_reads_our_output(tmp_path, raw_records):
    """The whole point: normalize.py must accept what we write."""
    path = tmp_path / "out.jsonl"
    # Add the minimum fields normalize.py projects.
    full = [
        {**r, "title": "t", "url": "u", "permalink": "/p/",
         "score": 1, "num_comments": 0}
        for r in raw_records
    ]
    write_jsonl(full, path)
    from etl.normalize import normalize_listing
    df = normalize_listing(path)
    assert set(df.id) == {"abc", "def"}


def test_write_jsonl_is_atomic(tmp_path, raw_records):
    """Writes to <path>.tmp then rename, so a crash cannot leave a torn file."""
    path = tmp_path / "out.jsonl"
    write_jsonl(raw_records, path)
    assert path.exists()
    assert not path.with_suffix(".jsonl.tmp").exists()
