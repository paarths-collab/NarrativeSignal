# Daily Refresh Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 2 fetcher and Phase 1 ETL to run nightly on GitHub Actions,
publish artifacts to a stateless orphan branch, and have the Render container pull them at
startup — with a baked-in seed fallback so a first boot always works.

**Architecture:**

```
GitHub Actions (nightly, cron)
│
├── checkout live-data-layer
├── python -m fetcher fetch --out out/day.jsonl
├── python -m etl.pipeline out/day.jsonl → 19 CSVs in artifacts/
├── validate: all files present, row counts sane, no zeros
└── force-push artifacts/ → orphan branch `data-live`

Render container startup (entrypoint.sh)
│
├── curl artifacts tarball from data-live → /app/data
├── on failure: fall back to /app/seed_data (already-baked-in snapshot)
├── rm -f cache artifacts that reference old data
├── python patch_db.py   (rebuild analysis_v2.db from CSVs)
└── exec uvicorn
```

**Tech stack:** GitHub Actions (`ubuntu-latest`), Python 3.11 (matches production), plain
`curl`/`tar`/`git` — no new runtime dependencies.

**Scope note:** This is Phase 3 of 6. It ends with a working nightly refresh: Actions
succeeds, `data-live` gets updated, and a container boot with `data-live` present pulls
new artifacts. **Not in scope:** cache invalidation of warm containers (Phase 4), the
globe pipeline (Phase 5), or LLM categorization of the 868-domain worklist (Phase 6).

**Blocks on Phase 2** — Task 4 here (the workflow YAML) invokes `python -m fetcher`, which
does not exist until Phase 2 T6 lands. Tasks 1–3 and 5–8 of THIS plan are independent of
Phase 2 and can be executed in parallel with it.

---

## Prerequisites — the human, not the code

**Two things I cannot do; you have to.** These are the only manual steps in the whole
project.

**A. Set four GitHub Actions secrets on `paarths-collab/NarrativeSignal`:**

| Secret | Where to get it |
|---|---|
| `REDDIT_CLIENT_ID` | reddit.com/prefs/apps → script app |
| `REDDIT_CLIENT_SECRET` | same |
| `REDDIT_USER_AGENT` | free-form; use `narrativesignal/1.0 by u/<you> (contact: <email>)` |
| `GROQ_API_KEY` | console.groq.com (Phase 6 uses this; set now so Phase 3's smoke test passes without a code change later) |

Settings → Secrets and variables → Actions → New repository secret. About 10 minutes.

**B. Once, initialize the orphan branch.** Task 8 of this plan documents how, but the
first push to `data-live` uses your credentials, not the workflow's. This is a one-command
step in the runbook.

**Both must be done before Task 6 (the smoke test) can pass.** Tasks 1–5 are pure code and
require nothing from you.

---

## File Structure

```
.github/
└── workflows/
    └── nightly-etl.yml           the cron + fetch + build + push job
scripts/
├── validate_artifacts.py         checks the 19 outputs before the push
├── publish_to_data_live.sh       orphan-branch force-push, isolated for testability
└── fetch_data_live.sh            container-side download with fallback
backend/
└── entrypoint.sh                 (modified) call fetch_data_live.sh, then patch_db
tests/
└── refresh/
    ├── __init__.py
    ├── test_workflow_yaml.py     schema and env-var wiring checks
    ├── test_validate_artifacts.py behavior of the gate
    ├── test_publish_script.py    dry-run over a fake repo
    └── test_entrypoint.py        entrypoint behaviour under both success and fallback
docs/
├── deploy-secrets.md             the runbook the human follows
└── daily-refresh-runbook.md      operator's manual (what to do when it breaks)
```

**Modified:** `backend/entrypoint.sh`, `.gitignore` (one line). **Not modified:**
`render.yaml`, the Dockerfile, any `etl/` or `fetcher/` code.

---

## Task dependency graph

```
T1 scaffolding + fixtures
 ├─ T2 validate_artifacts.py  (pure Python; testable in isolation)
 ├─ T3 publish_to_data_live.sh (git operations; testable against a temp repo)
 ├─ T5 fetch_data_live.sh      (container-side download + fallback)
 └─ T4 nightly-etl.yml         (depends on T2, T3; assumes fetcher exists — Phase 2 gate)
     └─ T6 entrypoint.sh integration + tests
         └─ T7 first end-to-end dry run (real Actions run, no push)

T8 runbooks & secrets docs (any time)
```

Model routing: **Opus 5** for T4 (workflow YAML — subtle secrets/permissions/error-handling
plumbing) and T6 (entrypoint changes to production boot path). Everything else Sonnet 5.

---

### Task 1: Scaffolding

**Files:**
- Create: `tests/refresh/__init__.py`, `scripts/__init__.py`,
  `tests/refresh/conftest.py`, `.github/workflows/.gitkeep`, `scripts/.gitkeep`

- [ ] **Step 1: Create empty dirs**

```bash
mkdir -p .github/workflows scripts tests/refresh
touch scripts/__init__.py tests/refresh/__init__.py
touch .github/workflows/.gitkeep scripts/.gitkeep
```

- [ ] **Step 2: Write `tests/refresh/conftest.py`**

```python
"""Shared fixtures for refresh tests: fake artifacts and a scratch git repo."""
import subprocess
from pathlib import Path

import pytest


REQUIRED = [
    "clean_posts.csv", "clean_with_clusters_v2.csv", "graph_edge_intelligence_table.csv",
    "narrative_intelligence_summary.csv", "narrative_spread_chain_table.csv",
    "narrative_topic_mapping.csv", "narrative_registry.csv",
    "narrative_diffusion_table.csv", "amplification_event_table.csv",
    "author_amplification_summary.csv", "author_influence_profile.csv",
    "bridge_authors_v2.csv", "user_intelligence_summary.csv",
    "subreddit_intelligence_summary.csv", "subreddit_domain_flow_v2.csv",
    "clean_top_distinctive_domains.csv", "echo_chamber_scores.csv",
    "daily_volume_v2.csv", "ideological_distance_matrix.csv",
]


@pytest.fixture()
def good_artifacts(tmp_path: Path) -> Path:
    """A directory populated with all 19 non-empty CSVs plus the datestamp."""
    out = tmp_path / "artifacts"
    out.mkdir()
    for name in REQUIRED:
        (out / name).write_text("id,x\na,1\nb,2\nc,3\n", encoding="utf-8")
    (out / "generated_at.txt").write_text("2026-07-29T00:00:00Z\n", encoding="utf-8")
    return out


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """A fresh git repo we can force-push into without touching anything real."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@ci.local"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=root, check=True)
    return root
```

- [ ] **Step 3: Confirm collection loads**

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/refresh --collect-only -q`
Expected: no tests, no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/refresh scripts .github/workflows
git commit -m "test(refresh): scaffolding for Phase 3 tests"
```

---

### Task 2: `scripts/validate_artifacts.py`

**Files:** Create `scripts/validate_artifacts.py`, `tests/refresh/test_validate_artifacts.py`.

The gate that runs after the ETL and before the orphan-branch push. Its whole job is to
refuse to publish something broken.

- [ ] **Step 1: Failing test**

```python
# tests/refresh/test_validate_artifacts.py
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_artifacts import ValidationError, validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_good_artifacts_pass(good_artifacts):
    validate(good_artifacts)  # must not raise


def test_missing_file_fails(good_artifacts):
    (good_artifacts / "clean_posts.csv").unlink()
    with pytest.raises(ValidationError, match="missing"):
        validate(good_artifacts)


def test_empty_file_fails(good_artifacts):
    (good_artifacts / "narrative_registry.csv").write_text("")
    with pytest.raises(ValidationError, match="empty"):
        validate(good_artifacts)


def test_zero_row_file_fails(good_artifacts):
    """Header alone is not enough — every CSV must have at least one data row."""
    (good_artifacts / "daily_volume_v2.csv").write_text(
        "created_datetime,post_count\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="no rows"):
        validate(good_artifacts)


def test_missing_generated_at_fails(good_artifacts):
    (good_artifacts / "generated_at.txt").unlink()
    with pytest.raises(ValidationError, match="generated_at"):
        validate(good_artifacts)


def test_cli_success_exits_zero(good_artifacts):
    r = subprocess.run(
        [sys.executable, "-m", "scripts.validate_artifacts", str(good_artifacts)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_cli_failure_exits_nonzero(good_artifacts):
    (good_artifacts / "clean_posts.csv").unlink()
    r = subprocess.run(
        [sys.executable, "-m", "scripts.validate_artifacts", str(good_artifacts)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "missing" in (r.stdout + r.stderr).lower()
```

Run: FAIL with `ModuleNotFoundError: No module named 'scripts.validate_artifacts'`.

- [ ] **Step 2: Write `scripts/validate_artifacts.py`**

```python
"""Refuse to publish broken artifacts. Runs after the ETL, before the orphan push."""
import argparse
import sys
from pathlib import Path


REQUIRED = [
    "clean_posts.csv", "clean_with_clusters_v2.csv", "graph_edge_intelligence_table.csv",
    "narrative_intelligence_summary.csv", "narrative_spread_chain_table.csv",
    "narrative_topic_mapping.csv", "narrative_registry.csv",
    "narrative_diffusion_table.csv", "amplification_event_table.csv",
    "author_amplification_summary.csv", "author_influence_profile.csv",
    "bridge_authors_v2.csv", "user_intelligence_summary.csv",
    "subreddit_intelligence_summary.csv", "subreddit_domain_flow_v2.csv",
    "clean_top_distinctive_domains.csv", "echo_chamber_scores.csv",
    "daily_volume_v2.csv", "ideological_distance_matrix.csv",
]


class ValidationError(RuntimeError):
    pass


def _count_rows(path: Path) -> int:
    """Data rows only (excludes header). Assumes UTF-8, standard CSV."""
    with path.open("r", encoding="utf-8") as fh:
        lines = sum(1 for _ in fh)
    return max(0, lines - 1)


def validate(directory: Path) -> None:
    directory = Path(directory)
    missing = [n for n in REQUIRED if not (directory / n).exists()]
    if missing:
        raise ValidationError(f"missing: {', '.join(missing)}")

    for name in REQUIRED:
        path = directory / name
        if path.stat().st_size == 0:
            raise ValidationError(f"{name}: empty file")
        if _count_rows(path) == 0:
            raise ValidationError(f"{name}: no rows (header only)")

    stamp = directory / "generated_at.txt"
    if not stamp.exists() or not stamp.read_text().strip():
        raise ValidationError("generated_at.txt missing or empty")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="validate_artifacts")
    p.add_argument("directory", type=Path)
    args = p.parse_args(argv)
    try:
        validate(args.directory)
    except ValidationError as e:
        print(f"[validate] FAIL: {e}", file=sys.stderr)
        return 1
    print(f"[validate] OK: {len(REQUIRED)} artifacts present, non-empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Pass, commit**

Expected: 7 passed.

```bash
git add scripts/validate_artifacts.py tests/refresh/test_validate_artifacts.py
git commit -m "feat(refresh): validate the 19 artifacts before publishing"
```

---

### Task 3: `scripts/publish_to_data_live.sh`

**Files:** Create `scripts/publish_to_data_live.sh`, `tests/refresh/test_publish_script.py`.

Orphan branches keep the `data-live` branch tiny — each push replaces history rather than
adding to it. Otherwise the branch would accumulate GB of nightly artifacts over months.

- [ ] **Step 1: Failing test (drives against a scratch repo)**

```python
# tests/refresh/test_publish_script.py
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "publish_to_data_live.sh"


def _run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


@pytest.fixture()
def bare_remote(tmp_path):
    """A bare repo that acts as `origin` for our scratch push."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    return remote


def test_publish_creates_orphan_branch(scratch_repo, bare_remote, good_artifacts):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)

    r = _run(["bash", str(SCRIPT), str(good_artifacts), "test-live"], cwd=scratch_repo)
    assert r.returncode == 0, r.stderr

    # `test-live` on the remote exists and has one commit
    listing = subprocess.run(
        ["git", "-C", str(bare_remote), "log", "--oneline", "test-live"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(listing) == 1, "orphan branch must always have exactly one commit"


def test_republishing_replaces_history(scratch_repo, bare_remote, good_artifacts):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)

    for _ in range(3):
        assert _run(["bash", str(SCRIPT), str(good_artifacts), "test-live"],
                    cwd=scratch_repo).returncode == 0

    log = subprocess.run(
        ["git", "-C", str(bare_remote), "log", "--oneline", "test-live"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(log) == 1, f"expected 1 commit after 3 publishes; got {len(log)}"


def test_publish_refuses_nonexistent_dir(scratch_repo, bare_remote, tmp_path):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)
    r = _run(["bash", str(SCRIPT), str(tmp_path / "nope"), "test-live"], cwd=scratch_repo)
    assert r.returncode != 0
```

Run: FAIL — `SCRIPT` does not exist.

- [ ] **Step 2: Write `scripts/publish_to_data_live.sh`**

```bash
#!/usr/bin/env bash
# publish_to_data_live.sh <artifacts_dir> <branch>
# Force-push <artifacts_dir> as the single commit of an orphan branch.
# Keeps the branch tiny even after months of nightly runs.

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <artifacts_dir> <branch>" >&2
  exit 2
fi

ARTIFACTS="$1"
BRANCH="$2"

if [ ! -d "$ARTIFACTS" ]; then
  echo "publish: $ARTIFACTS is not a directory" >&2
  exit 1
fi

WORKTREE="$(mktemp -d)"
trap 'rm -rf "$WORKTREE"' EXIT

# Isolate: build the branch in a throwaway worktree so the caller's tree is untouched.
git worktree add --detach "$WORKTREE" HEAD >/dev/null
(
  cd "$WORKTREE"
  git checkout --orphan "$BRANCH" >/dev/null
  git rm -rf . >/dev/null 2>&1 || true

  cp -a "$ARTIFACTS/." .

  git add -A
  git -c user.email=ci@narrativesignal.local -c user.name="ci" \
    commit -q -m "nightly artifacts $(date -u +%Y-%m-%dT%H:%M:%SZ)"

  git push -q --force origin "$BRANCH"
)
git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true

echo "publish: pushed $ARTIFACTS to origin/$BRANCH"
```

- [ ] **Step 3: Make executable, run tests, commit**

```bash
chmod +x scripts/publish_to_data_live.sh
./.venv-etl/Scripts/python.exe -m pytest tests/refresh/test_publish_script.py -q -W error
```

Expected: 3 passed.

```bash
git add scripts/publish_to_data_live.sh tests/refresh/test_publish_script.py
git commit -m "feat(refresh): orphan-branch publish script keeps data-live tiny"
```

---

### Task 4: `.github/workflows/nightly-etl.yml` (Opus)

**Files:** Create `.github/workflows/nightly-etl.yml`,
`tests/refresh/test_workflow_yaml.py`.

**Depends on Phase 2 T6** (`python -m fetcher` must exist). If Phase 2 is not landed when
this task runs, the workflow's smoke run in Task 6 will fail — that is intentional, the
YAML is still worth writing.

- [ ] **Step 1: Failing test — verifies the YAML wiring**

```python
# tests/refresh/test_workflow_yaml.py
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/nightly-etl.yml"


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_runs_on_schedule_and_dispatch(wf):
    triggers = wf.get(True) or wf.get("on")  # PyYAML parses `on:` as True
    assert "schedule" in triggers, "must have a cron schedule"
    assert "workflow_dispatch" in triggers, "must be manually runnable"


def test_cron_is_utc_and_daily(wf):
    triggers = wf.get(True) or wf.get("on")
    crons = [s["cron"] for s in triggers["schedule"]]
    assert len(crons) == 1
    hour_field = crons[0].split()[1]
    # Any single hour is fine; a wildcard (*) would fire hourly
    assert hour_field != "*"


def test_uses_python_311(wf):
    steps = wf["jobs"]["nightly"]["steps"]
    setup = next(s for s in steps if "actions/setup-python" in str(s.get("uses", "")))
    assert setup["with"]["python-version"].startswith("3.11")


def test_secrets_are_wired(wf):
    env = wf["jobs"]["nightly"].get("env", {})
    for secret in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
                   "REDDIT_USER_AGENT", "GROQ_API_KEY"):
        assert secret in env, f"missing env: {secret}"
        assert env[secret].startswith("${{ secrets."), env[secret]


def test_validation_gates_the_push(wf):
    steps = wf["jobs"]["nightly"]["steps"]
    names = [s.get("name", "") for s in steps]
    v_idx = next(i for i, n in enumerate(names) if "validate" in n.lower())
    p_idx = next(i for i, n in enumerate(names) if "publish" in n.lower()
                                                   or "data-live" in n.lower())
    assert v_idx < p_idx, "validate must run before publish"


def test_concurrency_prevents_overlap(wf):
    """Two runs simultaneously would race the orphan-branch push."""
    assert wf.get("concurrency"), "workflow must declare concurrency"


def test_needs_write_permission_on_contents(wf):
    perms = wf["jobs"]["nightly"].get("permissions", {})
    assert perms.get("contents") == "write", "must push to data-live"
```

Run: FAIL — file does not exist.

- [ ] **Step 2: Add PyYAML to the pinned venv only**

The workflow is validated in tests but PyYAML is not shipped with the app.

```bash
./.venv-etl/Scripts/python.exe -m pip install "PyYAML==6.0.2"
```

Do not add PyYAML to `requirements.txt` — the runtime does not need it.

- [ ] **Step 3: Write `.github/workflows/nightly-etl.yml`**

```yaml
name: nightly-etl

on:
  schedule:
    # 03:15 UTC — after most subreddits' daily activity has settled
    - cron: "15 3 * * *"
  workflow_dispatch:
    inputs:
      date:
        description: "UTC calendar day to fetch (YYYY-MM-DD, default yesterday)"
        required: false
        type: string

concurrency:
  group: nightly-etl
  cancel-in-progress: false

permissions:
  contents: write   # needed to force-push data-live

jobs:
  nightly:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      REDDIT_CLIENT_ID:     ${{ secrets.REDDIT_CLIENT_ID }}
      REDDIT_CLIENT_SECRET: ${{ secrets.REDDIT_CLIENT_SECRET }}
      REDDIT_USER_AGENT:    ${{ secrets.REDDIT_USER_AGENT }}
      GROQ_API_KEY:         ${{ secrets.GROQ_API_KEY }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install runtime deps
        run: |
          pip install -r requirements.txt

      - name: Fetch yesterday
        run: |
          mkdir -p out
          DATE="${{ inputs.date || '' }}"
          if [ -z "$DATE" ]; then
            python -m fetcher fetch --out out/day.jsonl
          else
            python -m fetcher fetch --date "$DATE" --out out/day.jsonl
          fi
          wc -l out/day.jsonl

      - name: Run ETL
        run: |
          mkdir -p artifacts
          python -c "
          from pathlib import Path
          from etl.normalize import normalize_listing
          from etl.pipeline import run_pipeline
          clean = normalize_listing(Path('out/day.jsonl'))
          run_pipeline(clean, Path('artifacts'),
                       categories_path=Path('data/domain_categories.json'))
          "
          date -u +%Y-%m-%dT%H:%M:%SZ > artifacts/generated_at.txt

      - name: Validate artifacts
        run: python -m scripts.validate_artifacts artifacts

      - name: Publish to data-live
        run: bash scripts/publish_to_data_live.sh artifacts data-live
```

- [ ] **Step 4: Pass, commit**

Expected: 7 passed.

```bash
git add .github/workflows/nightly-etl.yml tests/refresh/test_workflow_yaml.py
git commit -m "feat(refresh): nightly ETL workflow, secrets-driven, publishes to data-live"
```

---

### Task 5: `scripts/fetch_data_live.sh`

**Files:** Create `scripts/fetch_data_live.sh`,
`tests/refresh/test_fetch_data_live.py`.

Downloads the `data-live` tarball at container startup. Fails-open to the baked seed.

Use GitHub's `tarball` endpoint rather than `git clone` — Alpine/slim images may not have
git, and a tarball is one HTTP GET.

- [ ] **Step 1: Failing test — offline, uses a local HTTP server**

```python
# tests/refresh/test_fetch_data_live.py
import http.server
import socketserver
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "fetch_data_live.sh"


class _FakeServer:
    def __init__(self, root: Path):
        self.root = root
        self.port = 0
        self._srv = None
        self._thread = None

    def __enter__(self):
        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
            *a, directory=str(self.root), **kw
        )
        self._srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._srv.shutdown()


@pytest.fixture()
def artifacts_tarball(good_artifacts, tmp_path):
    """Package good_artifacts as GitHub does: repo-name-branch/... prefix."""
    tar_dir = tmp_path / "serve"
    tar_dir.mkdir()
    tar_path = tar_dir / "narrative.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(good_artifacts, arcname="paarths-collab-narrative-abcd/")
    return tar_dir, tar_path.name


def test_fetch_downloads_and_extracts(artifacts_tarball, tmp_path):
    tar_dir, tar_name = artifacts_tarball
    dest = tmp_path / "app-data"
    dest.mkdir()

    with _FakeServer(tar_dir) as srv:
        url = f"http://127.0.0.1:{srv.port}/{tar_name}"
        r = subprocess.run(
            ["bash", str(SCRIPT), url, str(dest), "/nonexistent/seed"],
            capture_output=True, text=True,
        )
    assert r.returncode == 0, r.stderr
    assert (dest / "clean_posts.csv").exists()


def test_fetch_falls_back_to_seed_when_url_fails(tmp_path, good_artifacts):
    dest = tmp_path / "app-data"
    dest.mkdir()

    r = subprocess.run(
        ["bash", str(SCRIPT), "http://127.0.0.1:1/nope.tar.gz",
         str(dest), str(good_artifacts)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (dest / "clean_posts.csv").exists()
    assert "fallback" in (r.stdout + r.stderr).lower()


def test_fetch_hard_fails_when_no_seed(tmp_path):
    dest = tmp_path / "app-data"
    dest.mkdir()

    r = subprocess.run(
        ["bash", str(SCRIPT), "http://127.0.0.1:1/nope.tar.gz",
         str(dest), "/nonexistent/seed"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0, "must fail loudly when both download AND seed fail"
```

Run: FAIL — script does not exist.

- [ ] **Step 2: Write `scripts/fetch_data_live.sh`**

```bash
#!/usr/bin/env bash
# fetch_data_live.sh <tarball_url> <dest_dir> <seed_dir>
# Try to download and extract; on any failure copy seed_dir → dest_dir.
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
```

- [ ] **Step 3: Make executable, pass, commit**

```bash
chmod +x scripts/fetch_data_live.sh
./.venv-etl/Scripts/python.exe -m pytest tests/refresh/test_fetch_data_live.py -q -W error
```

Expected: 3 passed.

```bash
git add scripts/fetch_data_live.sh tests/refresh/test_fetch_data_live.py
git commit -m "feat(refresh): container-side download of data-live with seed fallback"
```

---

### Task 6: `backend/entrypoint.sh` integration (Opus)

**Files:** Modify `backend/entrypoint.sh`. Create `tests/refresh/test_entrypoint.py`.

**Changes production boot path.** Careful. Existing behaviour is preserved as the fallback.

Current file (20 lines):
```sh
#!/bin/sh
set -e
DATA_PATH="${DATA_PATH:-/app/data}"
SEED_PATH="/app/seed_data"
mkdir -p "$DATA_PATH"
if [ -d "$SEED_PATH" ] && [ ! -f "$DATA_PATH/analysis_v2.db" ]; then
  echo "[entrypoint] Database missing in $DATA_PATH. Initializing from seed data..."
  cp -a "$SEED_PATH/." "$DATA_PATH/"
fi
echo "[entrypoint] Patching DuckDB intelligence views..."
python patch_db.py
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

- [ ] **Step 1: Failing test**

```python
# tests/refresh/test_entrypoint.py
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = REPO_ROOT / "backend" / "entrypoint.sh"


@pytest.fixture()
def fake_env(tmp_path, good_artifacts):
    """A fake /app layout with a seed and a patch_db.py that just touches a file."""
    app = tmp_path / "app"
    (app / "seed_data").mkdir(parents=True)
    (app / "data").mkdir()
    (app / "backend").mkdir()
    # copy good_artifacts into seed
    for f in good_artifacts.iterdir():
        shutil.copy2(f, app / "seed_data" / f.name)
    # stub patch_db.py: it just writes a marker
    (app / "backend" / "patch_db.py").write_text(
        "import os, pathlib; "
        "pathlib.Path(os.environ['DATA_PATH'], 'patched').write_text('ok')\n"
    )
    # stub uvicorn: don't actually start it; entrypoint's `exec` will fail cleanly
    (app / "backend" / "entrypoint.sh").write_text(ENTRY.read_text())
    (app / "backend" / "entrypoint.sh").chmod(0o755)
    return app


def _run_entry(app, extra_env=None):
    env = {
        **os.environ,
        "DATA_PATH": str(app / "data"),
        "SEED_PATH": str(app / "seed_data"),
        "FETCH_DATA_LIVE_URL": "http://127.0.0.1:1/nope.tar.gz",  # forces fallback
        "FETCH_DATA_LIVE_SCRIPT": str(REPO_ROOT / "scripts" / "fetch_data_live.sh"),
        "SKIP_UVICORN": "1",  # entrypoint respects this and exits cleanly
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(app / "backend" / "entrypoint.sh")],
        cwd=app / "backend", env=env, capture_output=True, text=True,
    )


def test_fallback_seeds_data_and_runs_patch_db(fake_env):
    r = _run_entry(fake_env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (fake_env / "data" / "clean_posts.csv").exists()
    assert (fake_env / "data" / "patched").exists()


def test_skip_uvicorn_is_respected(fake_env):
    """The test hook that lets us verify the pre-uvicorn boot path without a server."""
    r = _run_entry(fake_env)
    assert r.returncode == 0
    assert "uvicorn" not in r.stdout.lower(), "uvicorn must NOT have been invoked"
```

Run: FAIL — the current entrypoint does not know about `FETCH_DATA_LIVE_URL` or
`SKIP_UVICORN`.

- [ ] **Step 2: Rewrite `backend/entrypoint.sh`**

```bash
#!/bin/sh
# Container startup: fetch fresh artifacts from data-live, then start the app.
#
# The download is best-effort. On any failure the container falls back to the
# baked-in /app/seed_data (which is a copy of data/ from the image build).
# This means a first boot never has zero data.
#
# Environment:
#   DATA_PATH          persistent data mount   (default /app/data)
#   SEED_PATH          in-image fallback dir   (default /app/seed_data)
#   FETCH_DATA_LIVE_URL   tarball URL          (skip download if unset)
#   FETCH_DATA_LIVE_SCRIPT path to helper      (default /app/scripts/fetch_data_live.sh)
#   SKIP_UVICORN       exit before uvicorn     (test hook only, must be unset in prod)

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
```

- [ ] **Step 3: Also copy `scripts/` into the Docker image**

The container needs `fetch_data_live.sh` on disk. `backend/Dockerfile` currently copies
only `backend/`, so add one line.

Current Dockerfile has `COPY backend /app/backend` at line 15 (verify). Add immediately
after it:

```dockerfile
COPY scripts /app/scripts
```

And update the `chmod` line to include the fetch script:

```dockerfile
RUN chmod +x /app/backend/entrypoint.sh /app/scripts/fetch_data_live.sh
```

- [ ] **Step 4: Pass, commit**

```bash
./.venv-etl/Scripts/python.exe -m pytest tests/refresh/test_entrypoint.py -q -W error
```

Expected: 2 passed.

```bash
git add backend/entrypoint.sh backend/Dockerfile tests/refresh/test_entrypoint.py
git commit -m "feat(refresh): entrypoint fetches data-live, falls back to seed"
```

---

### Task 7: End-to-end dry run (documented, not automated)

**Files:** Create `docs/daily-refresh-runbook.md`.

The full path (real Reddit → Actions → data-live → container boot) can only be verified
with your credentials in the actual environment. This task documents that verification so
whoever runs it (you) knows exactly what to check.

- [ ] **Step 1: Write the runbook**

```markdown
# Daily Refresh Runbook

## First-run checklist

Before running the workflow for the first time, complete `docs/deploy-secrets.md`.

## Trigger a manual run

```
gh workflow run nightly-etl --repo paarths-collab/NarrativeSignal \
  --ref live-data-layer -f date=2025-02-17
```

Watch it in the Actions tab. Expected duration: **2–4 minutes**.

Success looks like:
1. **Fetch**: `wrote NNN records to out/day.jsonl` where NNN > 0
2. **Run ETL**: no traceback, `artifacts/` gets 19 CSV files
3. **Validate artifacts**: `OK: 19 artifacts present, non-empty`
4. **Publish to data-live**: `pushed artifacts to origin/data-live`

If any step fails, check that step's log — the messages are self-explanatory.

## Verify data-live got updated

```
git fetch narrativesignal data-live
git log narrativesignal/data-live --oneline -1
```

Should show a commit dated in the last few minutes with message
`nightly artifacts <timestamp>`.

## Verify the container picks it up

Redeploy on Render (or wait for the next natural restart). Watch the logs; expect:

```
[entrypoint] Fetching data-live from ...
[fetch_data_live] downloaded and extracted to /app/data
[entrypoint] Patching DuckDB intelligence views...
[patch] Imported: [...]
```

If the fetch fails, you'll see the fallback line and the container will still boot on
seed data — the site stays up on yesterday's numbers rather than going dark.

## What if the cron missed a night?

`workflow_dispatch` accepts a `date` input. Rerun for the missed day; only the last
push wins on `data-live`, which is fine — we care about the most recent day.

## What if the site is showing wrong data?

1. Check `data-live` timestamp (above).
2. If stale, run the workflow manually.
3. If fresh but wrong, check `/health` on the API — the container may be running on
   the fallback seed because the tarball download failed silently. Redeploy.
4. If still wrong, check the smoke tests in `tests/etl/` — they may reveal a
   regression the CI didn't catch.
```

- [ ] **Step 2: Commit**

```bash
git add docs/daily-refresh-runbook.md
git commit -m "docs(refresh): operator runbook for the daily refresh"
```

---

### Task 8: `docs/deploy-secrets.md`

**Files:** Create `docs/deploy-secrets.md`.

The one document I cannot execute. Point at it in every "how do I ship" question.

- [ ] **Step 1: Write the doc**

```markdown
# Deploy secrets

Four GitHub Actions secrets must be set on `paarths-collab/NarrativeSignal` before
the nightly refresh workflow can succeed. This is a one-time, ~10-minute task and
is the ONLY manual step in the entire live-data-layer rollout.

## 1. Reddit app

1. https://www.reddit.com/prefs/apps → **are you a developer? create an app**
2. Type: **script**
3. Name: `narrativesignal`
4. Redirect URI: `http://localhost:8080` (unused; must be non-empty)
5. Note the client id (14 chars under the app name) and secret

## 2. Groq key

1. https://console.groq.com → API Keys → **Create API Key**
2. Copy immediately; it is shown only once

## 3. Set the four GitHub secrets

https://github.com/paarths-collab/NarrativeSignal/settings/secrets/actions →
**New repository secret** for each:

| Name | Value |
|---|---|
| `REDDIT_CLIENT_ID` | from step 1 |
| `REDDIT_CLIENT_SECRET` | from step 1 |
| `REDDIT_USER_AGENT` | `narrativesignal/1.0 by u/<your-reddit-username> (contact: <your-email>)` |
| `GROQ_API_KEY` | from step 2 |

The User-Agent format matters. Reddit rate-limits generic UAs. The fetcher will
refuse to run with a placeholder value.

## 4. Initialize the data-live branch (once)

```
git checkout --orphan data-live
git rm -rf .
echo "placeholder for artifacts" > README.md
git add README.md
git -c user.email=you@you.com -c user.name=you commit -m "init data-live"
git push narrativesignal data-live
git checkout live-data-layer
```

After this, the workflow's `publish_to_data_live.sh` will keep overwriting it.

## 5. Verify with the workflow

`gh workflow run nightly-etl -f date=2025-02-17` and follow
`docs/daily-refresh-runbook.md`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/deploy-secrets.md
git commit -m "docs(refresh): setup runbook for the four GitHub secrets"
```

---

## Definition of Done

- [ ] `./.venv-etl/Scripts/python.exe -m pytest tests -q -W error` passes with the new
      refresh tests added on top of the baseline
- [ ] `.github/workflows/nightly-etl.yml` exists, references all four secrets, and gates
      the publish on validation
- [ ] `scripts/publish_to_data_live.sh` and `scripts/fetch_data_live.sh` exist, are
      executable, and are covered by offline tests
- [ ] `backend/entrypoint.sh` fetches data-live and falls back to seed
- [ ] `docs/deploy-secrets.md` and `docs/daily-refresh-runbook.md` exist

**Not in scope:** Warm-container refresh (Phase 4), globe pipeline (Phase 5), LLM
categorization worklist for the 868 domains (Phase 6).

**Post-landing verification** — requires your credentials, so it is a runbook item
rather than a task: trigger the workflow manually, watch `data-live` update, verify a
container reboot pulls the new artifacts.
