# Phase 4 Upstream Review Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the reviewed delta between the pinned upstream source and its observed new commit, and make strict verification reject an invalid review manifest.

**Architecture:** A checked-in JSON manifest records only reviewed skill-level changes. Each item is either `review` or `quarantined`; it never copies upstream content or changes `sources.lock.json`. The strict verifier validates provenance, unique source paths, allowed dispositions, and non-empty rationale.

**Tech Stack:** Python 3.12 standard library, existing strict verifier, pytest.

## Global Constraints

- The source lock and catalog stay unchanged.
- Entries carrying changed executable files are `quarantined`.
- New Markdown-only skill entries remain `review`, not Core and not imported.
- No network check runs inside strict verification.

---

### Task 1: Review manifest contract

**Files:**

- Create: `registry/upstream-review.json`
- Modify: `pipeline/skill_registry/validator.py`
- Test: `tests/contracts/test_validator.py`

**Interfaces:**

- Consumes: `registry/upstream-review.json` as `{ "schema_version": 1, "source_id": str, "pinned_commit": str, "observed_commit": str, "records": [{ "source_path": str, "change": "added"|"modified", "disposition": "review"|"quarantined", "reason": str }] }`.
- Produces: verifier finding `registry.upstream-review` for invalid data.

- [ ] **Step 1: Write the failing contract test**

```python
def test_strict_contract_rejects_invalid_upstream_review(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/upstream-review.json").read_text())
    payload["records"][0]["disposition"] = "accepted"
    (root / "registry/upstream-review.json").write_text(json.dumps(payload))
    assert any(item["check_id"] == "registry.upstream-review" for item in verify_repository(root).findings)
```

- [ ] **Step 2: Run it and confirm RED**

Run: `PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest tests/contracts/test_validator.py::test_strict_contract_rejects_invalid_upstream_review -q`

Expected: FAIL because the manifest is not validated.

- [ ] **Step 3: Add the minimal validator**

Require schema version 1, a locked source ID, matching pinned commit, valid 40-character observed commit, non-empty unique `skills/<name>` paths, allowed change/disposition values, and non-empty reasons.

- [ ] **Step 4: Run contract suite**

Run: `PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest tests/contracts/test_validator.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add registry/upstream-review.json pipeline/skill_registry/validator.py tests/contracts/test_validator.py
git commit -m "feat: record upstream review decisions"
```

### Task 2: Populate evidence and document the decision

**Files:**

- Modify: `docs/migration-from-agentic-library.md`
- Modify: `registry/upstream-review.json`

- [ ] **Step 1: Record the 15 reviewed skill changes**

Use observed commit `5e31f236726a988e833b39215d140b2173bf05c0` against pinned commit `82c86e65677aa1b40fa8207f95bc43766494a3db`.

- [ ] **Step 2: State the safety boundary**

Document that 10 new Markdown-only skills and 3 modified Markdown-only skills are review candidates; 2 changed skills carrying executable changes remain quarantined. Nothing is imported or promoted.

- [ ] **Step 3: Verify**

Run: `PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli verify --strict`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/migration-from-agentic-library.md registry/upstream-review.json
git commit -m "docs: record upstream review boundary"
```

### Task 3: Final verification and PR

- [ ] **Step 1: Run the quality gate**

```bash
git diff --check
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m pytest -q
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli verify --strict
PYTHONPATH=pipeline /tmp/asr-plan1-venv/bin/python -m skill_registry.cli refresh --format json
```

Expected: clean diff, passing tests and verifier; refresh still reports the secondary source as behind.

- [ ] **Step 2: Push and open draft PR**

```bash
git push -u origin phase4/upstream-review
```

Do not merge until Boss approves review.
