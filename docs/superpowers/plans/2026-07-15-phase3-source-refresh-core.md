# Phase 3 Source Refresh and Core Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use \`superpowers:executing-plans\` to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add a read-only source freshness report and a verified Core admission gate without changing catalog content or promoting unaudited skills.

**Architecture:** The refresh command reads only \`registry/sources.lock.json\` and calls \`git ls-remote <url> HEAD\`; it reports the immutable pinned commit versus the observed remote commit and never edits the lock or catalog. A \`registry/core.json\` manifest is the only Core admission surface; the strict verifier rejects unknown, unsafe, duplicated, or non-active records. Because all migrated records are currently \`risk: unknown\`, the initial manifest is intentionally empty.

**Tech Stack:** Python 3.12 standard library, existing PyYAML verifier, pytest, Git CLI.

## Global Constraints

- No source commit may be changed unless matching catalog content is imported and re-hashed in a later reviewed change.
- Source checks are read-only; network failures return a non-zero command status and do not mutate registry files.
- Core may contain only active records whose \`risk\` is exactly \`safe\`; an empty Core manifest is valid.
- No new dependency or automatic skill promotion.

---

### Task 1: Source refresh report

**Files:**

- Create: \`pipeline/skill_registry/refresh.py\`
- Modify: \`pipeline/skill_registry/cli.py\`
- Test: \`tests/unit/test_refresh.py\`
- Test: \`tests/integration/test_refresh_cli.py\`

**Interfaces:**

- Produces: \`refresh_sources(root: Path, runner: Callable[..., str] = subprocess.check_output) -> dict[str, object]\`.
- Produces: CLI \`skill-registry refresh --root PATH --format json\`.

- [ ] **Step 1: Write the failing unit test**

\`\`\`python
def test_refresh_marks_a_changed_remote_as_behind(tmp_path):
    write_lock(tmp_path, commit="a" * 40)
    report = refresh_sources(tmp_path, runner=lambda *_: f"{'b' * 40}\tHEAD\n")
    assert report["sources"][0]["status"] == "behind"
    assert report["sources"][0]["observed_commit"] == "b" * 40
\`\`\`

- [ ] **Step 2: Run the test to verify it fails**

Run: \`PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest tests/unit/test_refresh.py -q\`

Expected: FAIL because \`skill_registry.refresh\` does not exist.

- [ ] **Step 3: Write minimal implementation**

\`\`\`python
def refresh_sources(root: Path, runner=subprocess.check_output) -> dict[str, object]:
    sources = json.loads((root / "registry/sources.lock.json").read_text())["sources"]
    records = []
    for source in sources:
        output = runner(["git", "ls-remote", source["url"], "HEAD"], text=True)
        observed = output.split()[0]
        records.append({"source_id": source["source_id"], "pinned_commit": source["commit"], "observed_commit": observed, "status": "current" if observed == source["commit"] else "behind"})
    return {"sources": records}
\`\`\`

- [ ] **Step 4: Add the CLI parser and JSON/text rendering; test command exit behavior**

Run: \`PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest tests/unit/test_refresh.py tests/integration/test_refresh_cli.py -q\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add pipeline/skill_registry/refresh.py pipeline/skill_registry/cli.py tests/unit/test_refresh.py tests/integration/test_refresh_cli.py
git commit -m "feat: report pinned source freshness"
\`\`\`

### Task 2: Core admission manifest and strict contract

**Files:**

- Create: \`registry/core.json\`
- Modify: \`pipeline/skill_registry/validator.py\`
- Test: \`tests/contracts/test_validator.py\`

**Interfaces:**

- Consumes: \`registry/core.json\` as \`{ "schema_version": 1, "skill_ids": [str, ...] }\`.
- Produces: verifier finding \`registry.core\` when Core contains invalid records.

- [ ] **Step 1: Write the failing contract test**

\`\`\`python
def test_strict_contract_rejects_core_record_that_is_not_safe(repo_root, tmp_path):
    root = copy_repo(repo_root, tmp_path)
    record = json.loads((root / "registry/skills.json").read_text())["skills"][0]
    (root / "registry/core.json").write_text(json.dumps({"schema_version": 1, "skill_ids": [record["skill_id"]]}))
    checks = {item["check_id"] for item in verify_repository(root).findings}
    assert "registry.core" in checks
\`\`\`

- [ ] **Step 2: Run the test to verify it fails**

Run: \`PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest tests/contracts/test_validator.py::test_strict_contract_rejects_core_record_that_is_not_safe -q\`

Expected: FAIL because \`core.json\` is not enforced.

- [ ] **Step 3: Write minimal implementation**

Read \`core.json\` directly, require schema version 1, require a unique list of strings, and reject every member unless the matching skill has \`state == "active"\` and \`risk == "safe"\`. Add \`core.json\` to required registry files. Initialize it with an empty \`skill_ids\` list.

- [ ] **Step 4: Run contracts and complete suite**

Run: \`PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest -q\`

Expected: PASS. The initial Core has zero members because every imported skill remains \`unknown\` risk.

- [ ] **Step 5: Commit**

\`\`\`bash
git add registry/core.json pipeline/skill_registry/validator.py tests/contracts/test_validator.py
git commit -m "feat: gate Core admission on safe records"
\`\`\`

### Task 3: Document the operational boundary

**Files:**

- Modify: \`docs/migration-from-agentic-library.md\`

- [ ] **Step 1: Add the exact operator commands**

\`\`\`markdown
PYTHONPATH=pipeline python -m skill_registry.cli refresh --format json
PYTHONPATH=pipeline python -m skill_registry.cli verify --strict
\`\`\`

State that \`refresh\` reports only; it does not update the lock. State that Core admission requires a separately reviewed \`risk: safe\` classification.

- [ ] **Step 2: Verify commands against the two pinned sources**

Run: \`PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli refresh --format json\`

Expected: \`legacy-local\` is current at \`a3f3ac3...\`; the secondary source reports its observed current commit without changing the pinned lock.

- [ ] **Step 3: Commit**

\`\`\`bash
git add docs/migration-from-agentic-library.md
git commit -m "docs: explain source refresh and Core gate"
\`\`\`

### Task 4: Final verification and PR

- [ ] **Step 1: Run the full quality gate**

\`\`\`bash
git diff --check
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest -q
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli verify --strict
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli refresh --format json
\`\`\`

Expected: all tests pass; strict verification passes; refresh reports source state without file mutation.

- [ ] **Step 2: Push branch and open draft PR**

\`\`\`bash
git push -u origin phase3/source-refresh-core
\`\`\`

Open a draft PR targeting \`main\`. Do not merge until Boss approves the review.

