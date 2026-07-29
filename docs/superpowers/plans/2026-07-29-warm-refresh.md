# Phase 4: Warm-Container Refresh Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** After the nightly workflow publishes new artifacts, the running container picks
them up — not "next time it happens to restart."

**Architecture:** Cheap and correct: extend `nightly-etl.yml` to POST to a Render deploy
hook after the artifact push succeeds. Render restarts the container, `entrypoint.sh` runs
and downloads the fresh `data-live` tarball, module-level caches vanish along with the old
process. ~30s of 502s at 03:15 UTC, which nobody sees.

**Tech stack:** GitHub Actions curl step + one new secret. No runtime code changes.

**Scope note:** Everything zero-downtime — admin endpoints, APScheduler, in-process cache
invalidation — is out of scope. If restart-based refresh ever proves too disruptive, that's
a follow-up phase.

---

### Task 1: Add the redeploy step to `nightly-etl.yml`

**Files:** Modify `.github/workflows/nightly-etl.yml`. Add test to
`tests/refresh/test_workflow_yaml.py`.

The step must be **gated on the secret being present** — if `RENDER_DEPLOY_HOOK` isn't set,
the workflow succeeds without redeploy so users who haven't set it aren't blocked.

- [ ] **Step 1: Failing test — add to `tests/refresh/test_workflow_yaml.py`**

```python
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
```

- [ ] **Step 2: Add the step to `.github/workflows/nightly-etl.yml`** — append after the
  "Publish to data-live" step:

```yaml
      - name: Trigger Render redeploy
        env:
          HOOK_URL: ${{ secrets.RENDER_DEPLOY_HOOK }}
        run: |
          if [ -z "$HOOK_URL" ]; then
            echo "RENDER_DEPLOY_HOOK not set; skipping redeploy trigger"
            exit 0
          fi
          # Deploy hooks accept a POST with no body; response is JSON with a deploy id
          curl -fsS -X POST "$HOOK_URL"
          echo
```

- [ ] **Step 3:** `./.venv-etl/Scripts/python.exe -m pytest tests/refresh/test_workflow_yaml.py -q -W error`
  Expect 9 passed (7 pre-existing + 2 new).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/nightly-etl.yml tests/refresh/test_workflow_yaml.py
git commit -m "feat(refresh): trigger Render redeploy after publishing to data-live"
```

---

### Task 2: Document the new secret

**Files:** Modify `docs/deploy-secrets.md`.

- [ ] **Step 1:** Append a new numbered section after the existing "Set the four GitHub
  secrets" section:

```markdown
## 5. Optional: Render deploy hook

Without this, `data-live` will be updated nightly but your container will only see the new
data on its next natural restart. Setting this triggers an immediate redeploy after each
successful publish — ~30 seconds of downtime at 03:15 UTC, and your site is fresh.

1. Render dashboard → your service → **Settings** → **Deploy Hook**
2. Copy the URL (it embeds a per-service token; treat it like a credential)
3. GitHub → **Settings → Secrets → Actions → New repository secret**:
   - Name: `RENDER_DEPLOY_HOOK`
   - Value: the URL from step 2

If you skip this, everything else still works — you just get a stale site until the
container is bounced.
```

- [ ] **Step 2: Commit**

```bash
git add docs/deploy-secrets.md
git commit -m "docs: RENDER_DEPLOY_HOOK for warm-container refresh"
```

---

### Task 3: Document verification in the runbook

**Files:** Modify `docs/daily-refresh-runbook.md`.

- [ ] **Step 1:** After the "Verify data-live got updated" section, insert:

```markdown
## Verify the container redeployed (if RENDER_DEPLOY_HOOK is set)

The workflow's final step logs `Trigger Render redeploy` — expect a 200 OK JSON response.
Then on Render → your service → **Events**, a new deploy should appear within a minute of
the workflow completing. Once its status flips to `Live`, hit `/health` on the API — the
first response after restart will include the freshest data.

If the step logs `RENDER_DEPLOY_HOOK not set; skipping redeploy trigger`, that's the
optional path. Redeploy Render manually to pick up the new artifacts.
```

- [ ] **Step 2: Commit**

```bash
git add docs/daily-refresh-runbook.md
git commit -m "docs(refresh): document RENDER_DEPLOY_HOOK verification"
```

---

## Definition of Done

- [ ] Workflow tests pass 9/9
- [ ] Redeploy step exists, gates on the secret, runs after publish
- [ ] deploy-secrets.md documents section 5
- [ ] runbook describes the verification path
