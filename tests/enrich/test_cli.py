"""Tests for `enrich.cli` / `python -m enrich`.

No real LLM calls happen here: the injectable default classifier/generator
(`enrich.categorize_domains._default_classify`, `enrich.label_topics.generate`)
is monkeypatched to a fake in every test that exercises `categorize` or
`label`. `data/` is only ever read, never written -- all output goes to
`tmp_path`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ---------------------------------------------------------------------------
# --help works without secrets
# ---------------------------------------------------------------------------


def _env_without_secrets() -> dict[str, str]:
    """Mirrors the fetcher CLI test pattern: scrub the secrets this package
    needs (GROQ_API_KEY/GEMINI_API_KEY), not PATH -- scrubbing PATH breaks
    subprocess launching on Windows before argparse ever runs."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("GROQ_API_KEY", "GEMINI_API_KEY")
    }


def test_help_top_level_works_without_secrets() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "enrich", "--help"],
        capture_output=True, text=True, env=_env_without_secrets(),
    )
    assert r.returncode == 0, r.stderr
    assert "categorize" in r.stdout
    assert "label" in r.stdout
    assert "all" in r.stdout


@pytest.mark.parametrize("subcommand", ["categorize", "label", "all"])
def test_help_subcommand_works_without_secrets(subcommand: str) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "enrich", subcommand, "--help"],
        capture_output=True, text=True, env=_env_without_secrets(),
    )
    assert r.returncode == 0, r.stderr
    assert "--in" in r.stdout
    assert "--out" in r.stdout


# ---------------------------------------------------------------------------
# categorize
# ---------------------------------------------------------------------------


def test_categorize_cli_produces_expected_json(tmp_path, monkeypatch, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()

    (in_dir / "domain_categories.json").write_text(
        json.dumps(
            {
                "legacy.example": "Media / News",
                "already.example": "Government",
            }
        ),
        encoding="utf-8",
    )
    (in_dir / "uncategorized_domains.txt").write_text(
        "fresh-a.com\nfresh-b.org\n\n  \nfresh-a.com\n", encoding="utf-8"
    )

    answers = {"fresh-a.com": "News", "fresh-b.org": "Advocacy"}

    def fake_classify(prompt: str, choices: list[str]) -> str:
        for dom, label in answers.items():
            if f'"{dom}"' in prompt:
                return label
        raise AssertionError(f"unexpected prompt: {prompt!r}")

    monkeypatch.setattr(
        "enrich.categorize_domains._default_classify", fake_classify
    )

    from enrich.cli import run

    rc = run(["categorize", "--in", str(in_dir), "--out", str(out_dir)])
    assert rc == 0

    out_path = out_dir / "domain_categories.json"
    assert out_path.exists()
    result = json.loads(out_path.read_text(encoding="utf-8"))
    assert result == {
        "legacy.example": "News",  # migrated from "Media / News"
        "already.example": "Government",
        "fresh-a.com": "News",
        "fresh-b.org": "Advocacy",
    }

    captured = capsys.readouterr()
    # 2 distinct fresh domains asked (duplicate "fresh-a.com" collapsed),
    # nothing capped, nothing was already cached.
    assert "categorize: asked=2 skipped=0 cached=0" in captured.out

    # Input file must be untouched -- only `out` is written.
    original = json.loads((in_dir / "domain_categories.json").read_text(encoding="utf-8"))
    assert original == {
        "legacy.example": "Media / News",
        "already.example": "Government",
    }


def test_categorize_cli_reports_cached_domains(tmp_path, monkeypatch, capsys):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()

    (in_dir / "domain_categories.json").write_text(
        json.dumps({"cached.example": "News"}), encoding="utf-8"
    )
    (in_dir / "uncategorized_domains.txt").write_text(
        "cached.example\nnew.example\n", encoding="utf-8"
    )

    def fake_classify(prompt: str, choices: list[str]) -> str:
        assert '"new.example"' in prompt
        return "Blogs"

    monkeypatch.setattr(
        "enrich.categorize_domains._default_classify", fake_classify
    )

    from enrich.cli import run

    rc = run(["categorize", "--in", str(in_dir), "--out", str(out_dir)])
    assert rc == 0

    captured = capsys.readouterr()
    assert "categorize: asked=1 skipped=0 cached=1" in captured.out

    result = json.loads((out_dir / "domain_categories.json").read_text(encoding="utf-8"))
    assert result == {"cached.example": "News", "new.example": "Blogs"}


def test_categorize_cli_handles_missing_worklist_file(tmp_path, monkeypatch, capsys):
    """No `uncategorized_domains.txt` at all -> empty worklist, no crash."""
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "domain_categories.json").write_text(
        json.dumps({"only.example": "News"}), encoding="utf-8"
    )

    def fake_classify(prompt: str, choices: list[str]) -> str:
        raise AssertionError("classify should not be called")

    monkeypatch.setattr(
        "enrich.categorize_domains._default_classify", fake_classify
    )

    from enrich.cli import run

    rc = run(["categorize", "--in", str(in_dir), "--out", str(out_dir)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "categorize: asked=0 skipped=0 cached=0" in captured.out


# ---------------------------------------------------------------------------
# label
# ---------------------------------------------------------------------------


def test_label_cli_produces_csv_with_real_labels(tmp_path, monkeypatch, capsys):
    topic_map_df = pd.read_csv(DATA_DIR / "narrative_topic_mapping.csv")
    n_clusters = topic_map_df["topic_cluster"].nunique()

    fake_labels = iter(
        [f"Fake Label {i}" for i in range(n_clusters)]
    )

    def fake_generate(prompt: str) -> str:
        return next(fake_labels)

    monkeypatch.setattr("enrich.label_topics.generate", fake_generate)

    out_dir = tmp_path / "out"

    from enrich.cli import run

    rc = run(["label", "--in", str(DATA_DIR), "--out", str(out_dir)])
    assert rc == 0

    out_path = out_dir / "narrative_topic_mapping.csv"
    assert out_path.exists()
    result = pd.read_csv(out_path)

    assert list(result.columns) == ["narrative_id", "topic_cluster", "topic_label"]
    assert len(result) == len(topic_map_df)
    assert not result["topic_label"].astype(str).str.startswith("LABEL_ME_").any()

    captured = capsys.readouterr()
    assert f"label: labelled={n_clusters} clusters, N_missed=0" in captured.out

    # The committed source CSV under data/ must never be modified.
    untouched = pd.read_csv(DATA_DIR / "narrative_topic_mapping.csv")
    assert untouched["topic_label"].astype(str).str.startswith("LABEL_ME_").all()


def test_label_cli_reports_missed_clusters_on_partial_failure(tmp_path, monkeypatch, capsys):
    topic_map_df = pd.read_csv(DATA_DIR / "narrative_topic_mapping.csv")
    n_clusters = topic_map_df["topic_cluster"].nunique()

    call_count = {"n": 0}

    def flaky_generate(prompt: str) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated failure for the first cluster")
        return "Some Label"

    monkeypatch.setattr("enrich.label_topics.generate", flaky_generate)

    out_dir = tmp_path / "out"

    from enrich.cli import run

    rc = run(["label", "--in", str(DATA_DIR), "--out", str(out_dir)])
    assert rc == 0

    captured = capsys.readouterr()
    assert f"label: labelled={n_clusters} clusters, N_missed=1" in captured.out


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


def _seed_categorize_inputs(in_dir: Path) -> None:
    (in_dir / "domain_categories.json").write_text(
        json.dumps({"existing.example": "News"}), encoding="utf-8"
    )
    (in_dir / "uncategorized_domains.txt").write_text(
        "new-one.example\n", encoding="utf-8"
    )


def _seed_label_inputs(in_dir: Path) -> None:
    import shutil

    shutil.copy(DATA_DIR / "narrative_topic_mapping.csv", in_dir / "narrative_topic_mapping.csv")
    shutil.copy(
        DATA_DIR / "narrative_intelligence_summary.csv",
        in_dir / "narrative_intelligence_summary.csv",
    )


def test_all_runs_categorize_then_label_in_order(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _seed_categorize_inputs(in_dir)
    _seed_label_inputs(in_dir)

    call_order: list[str] = []

    def fake_classify(prompt: str, choices: list[str]) -> str:
        call_order.append("classify")
        return "Other"

    def fake_generate(prompt: str) -> str:
        call_order.append("generate")
        return "Some Label"

    monkeypatch.setattr("enrich.categorize_domains._default_classify", fake_classify)
    monkeypatch.setattr("enrich.label_topics.generate", fake_generate)

    from enrich.cli import run

    rc = run(["all", "--in", str(in_dir), "--out", str(out_dir)])
    assert rc == 0

    assert "classify" in call_order
    assert "generate" in call_order
    last_classify = max(i for i, c in enumerate(call_order) if c == "classify")
    first_generate = min(i for i, c in enumerate(call_order) if c == "generate")
    assert last_classify < first_generate, "categorize must fully complete before label starts"

    assert (out_dir / "domain_categories.json").exists()
    assert (out_dir / "narrative_topic_mapping.csv").exists()


def test_all_still_runs_label_when_categorize_raises(tmp_path, monkeypatch):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _seed_categorize_inputs(in_dir)
    _seed_label_inputs(in_dir)

    def raising_categorize(store, worklist, **kwargs):
        raise RuntimeError("simulated categorize crash")

    label_called = {"yes": False}

    def fake_generate(prompt: str) -> str:
        label_called["yes"] = True
        return "Some Label"

    monkeypatch.setattr("enrich.cli.categorize", raising_categorize)
    monkeypatch.setattr("enrich.label_topics.generate", fake_generate)

    from enrich.cli import run

    rc = run(["all", "--in", str(in_dir), "--out", str(out_dir)])

    # Overall failure is surfaced (categorize crashed)...
    assert rc != 0
    # ...but label still ran and wrote its output despite categorize's crash.
    assert label_called["yes"]
    assert (out_dir / "narrative_topic_mapping.csv").exists()
    # categorize never got to write its output.
    assert not (out_dir / "domain_categories.json").exists()
