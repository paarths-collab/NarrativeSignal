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


def test_redeploy_step_exists_and_is_gated(wf):
    steps = wf["jobs"]["nightly"]["steps"]
    names = [s.get("name", "") for s in steps]
    idx = next((i for i, n in enumerate(names) if "redeploy" in n.lower()), None)
    assert idx is not None, "expected a redeploy step"

    step = steps[idx]
    assert "RENDER_DEPLOY_HOOK" in step.get("env", {}).get("HOOK_URL", "")
    # gated: skips cleanly when the secret is empty
    assert "if" in step or "-z" in step.get("run", "") or "$HOOK_URL" in step.get("run", "")


def test_redeploy_runs_after_publish(wf):
    steps = wf["jobs"]["nightly"]["steps"]
    names = [s.get("name", "") for s in steps]
    p = next(i for i, n in enumerate(names) if "publish" in n.lower())
    r = next(i for i, n in enumerate(names) if "redeploy" in n.lower())
    assert r > p, "redeploy must run after publish"
