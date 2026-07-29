"""Serialize raw Reddit records to JSONL byte-compatible with etl/normalize.py."""
import json
import os
from pathlib import Path
from typing import Iterable


def to_jsonl_bytes(records: Iterable[dict]) -> bytes:
    """Emit `{"kind":"t3","data":{...}}\\n` per record, UTF-8, LF endings.

    Matches the shape etl/normalize.py's `pd.json_normalize` consumes after
    stripping the outer wrapper.
    """
    lines = []
    for rec in records:
        lines.append(json.dumps({"kind": "t3", "data": rec}, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_jsonl(records: Iterable[dict], path: Path) -> None:
    """Write atomically: <path>.tmp then os.replace, so a crash never leaves a torn file.

    Consumers on the same filesystem see either the old file or the complete new one,
    never a half-written one. The ETL runs against `<path>`, so a torn file would
    cascade into wrong artifacts.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(to_jsonl_bytes(records))
    os.replace(tmp, path)
