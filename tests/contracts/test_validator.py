import json
import shutil

import pytest

from skill_registry.validator import verify_repository
from skill_registry.identity import stable_skill_id
from skill_registry.integration import build_librarian_integration_lock


def clone_repository_fixture(repo_root, tmp_path):
    shutil.copytree(repo_root / "catalog", tmp_path / "catalog")
    shutil.copytree(repo_root / "registry", tmp_path / "registry")
    shutil.copytree(
        repo_root / "skills/skill-librarian",
        tmp_path / "skills/skill-librarian",
    )
    return tmp_path


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def check_ids(root):
    return {finding["check_id"] for finding in verify_repository(root).findings}


def copy_librarian_index(repo_root, root):
    shutil.copy2(repo_root / "librarian-index.json", root / "librarian-index.json")


def write_safety_profiles(root, **first_profile_changes):
    skills = json.loads((root / "registry/skills.json").read_text())["skills"]
    profiles = [
        {
            "skill_id": record["skill_id"],
            "content_sha256": record["content_sha256"],
            "scanner_version": 1,
            "status": "scanned",
            "signals": [],
            "severity": "clean",
            "evidence": [],
        }
        for record in sorted(
            (record for record in skills if record["state"] == "active"),
            key=lambda record: record["skill_id"],
        )
    ]
    profiles[0].update(first_profile_changes)
    write_json(
        root / "registry/safety-signals.json",
        {"schema_version": 1, "profiles": profiles},
    )


def reviewed_source_artifact(root):
    lock_path = root / "registry/sources.lock.json"
    lock = json.loads(lock_path.read_text())
    for source in lock["sources"]:
        source["review"] = {
            "status": "legacy",
            "reason": "predates-reviewed-intake",
        }
    source = next(
        item
        for item in lock["sources"]
        if item["source_id"] == "microsoftdocs-agent-skills"
    )
    artifact_relative = (
        "registry/source-reviews/"
        f"{source['source_id']}-{source['commit']}.json"
    )
    artifact = {
        "schema_version": 1,
        "source_id": source["source_id"],
        "source_commit": source["commit"],
        "manifest_sha256": "a" * 64,
        "candidate_count": 1,
        "decisions": [{
            "source_path": "skills/azure-blob-storage",
            "content_sha256": "b" * 64,
            "decision": "import",
            "taxonomy": "devops-and-security/azure-cloud",
            "category_fine": "cloud",
            "canonical_skill_id": None,
            "reason": "Reviewed fixture candidate",
        }],
    }
    source["review"] = {
        "status": "reviewed",
        "artifact": artifact_relative,
        "manifest_sha256": artifact["manifest_sha256"],
    }
    artifact_path = root / artifact_relative
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(artifact_path, artifact)
    write_json(lock_path, lock)
    return source, artifact_path, artifact


def test_complete_repository_passes(repo_root):
    report = verify_repository(repo_root)
    assert report.result == "pass"
    assert report.failed == 0
    assert report.skipped == 0


def test_strict_verifier_rejects_non_list_risk_reasons(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    write_safety_profiles(root)
    payload = json.loads((root / "registry/skills.json").read_text())
    payload["skills"][0]["risk_reasons"] = "not-a-list"
    write_json(root / "registry/skills.json", payload)

    assert verify_repository(root).result == "fail"


def test_strict_verifier_rejects_profile_with_stale_hash(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    write_safety_profiles(root, content_sha256="0" * 64)

    assert verify_repository(root).result == "fail"


def test_strict_verifier_rejects_boolean_scanner_version(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    write_safety_profiles(root, scanner_version=True)

    assert verify_repository(root).result == "fail"


def test_strict_verifier_rejects_low_prompt_injection_profile(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    write_safety_profiles(
        root,
        signals=["prompt_injection"],
        severity="low",
    )

    assert verify_repository(root).result == "fail"


def test_strict_verifier_rejects_clean_scan_error_profile(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    write_safety_profiles(root, status="scan_error", severity="clean")

    assert verify_repository(root).result == "fail"


@pytest.mark.parametrize(
    "mutation",
    ["missing_manifest", "lock_mismatch", "native_skill_mismatch"],
)
def test_verify_rejects_invalid_librarian_integration(
    repo_root, tmp_path, mutation
):
    root = clone_repository_fixture(repo_root, tmp_path)
    if mutation == "missing_manifest":
        (root / "registry/librarian-integration.json").unlink()
    elif mutation == "lock_mismatch":
        lock_path = root / "registry/librarian-integration.lock.json"
        lock = json.loads(lock_path.read_text())
        lock["manifest_sha256"] = "0" * 64
        write_json(lock_path, lock)
    else:
        (root / "skills/skill-librarian/SKILL.md").write_text(
            "changed", encoding="utf-8"
        )

    assert "registry.librarian-integration" in check_ids(root)


@pytest.mark.parametrize(
    "mutation",
    ["changed_reference", "extra_reference", "reference_symlink"],
)
def test_verify_rejects_librarian_reference_bundle_mutations(
    repo_root, tmp_path, mutation
):
    root = clone_repository_fixture(repo_root, tmp_path)
    references = root / "skills/skill-librarian/references"
    references.mkdir(exist_ok=True)
    reference = references / "control-plane.md"

    if mutation == "changed_reference":
        reference.write_text("changed", encoding="utf-8")
    elif mutation == "extra_reference":
        (references / "unexpected.md").write_text("extra", encoding="utf-8")
    else:
        external = tmp_path.parent / f"{tmp_path.name}-control-plane.md"
        external.write_text("external", encoding="utf-8")
        if reference.exists() or reference.is_symlink():
            reference.unlink()
        reference.symlink_to(external)

    assert "registry.librarian-integration" in check_ids(root)


def test_verify_rejects_missing_librarian_reference(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    references = root / "skills/skill-librarian/references"
    references.mkdir(exist_ok=True)
    for name in (
        "control-plane.md",
        "trust-and-safety.md",
        "composition.md",
        "decision-trace.md",
        "source-intake.md",
        "evaluation.md",
    ):
        (references / name).write_text(name, encoding="utf-8")
    write_json(
        root / "registry/librarian-integration.lock.json",
        build_librarian_integration_lock(root),
    )
    (references / "decision-trace.md").unlink()

    assert "registry.librarian-integration" in check_ids(root)


@pytest.mark.parametrize(
    "relative",
    [
        "registry/skills.json",
        "registry/core.json",
        "registry/sources.lock.json",
        "librarian-index.json",
    ],
)
def test_verify_reports_malformed_json_without_raising(
    repo_root, tmp_path, relative
):
    root = clone_repository_fixture(repo_root, tmp_path)
    if relative == "librarian-index.json":
        (root / relative).write_bytes((repo_root / relative).read_bytes())
    (root / relative).write_text("{")

    report = verify_repository(root)

    assert report.result == "fail"
    assert "registry.input" in {
        item["check_id"] for item in report.findings
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate_flat_name",
        "wrong_count",
        "extra_name",
        "missing_name",
        "malformed_entry",
        "skill_id_mismatch",
    ],
)
def test_verify_rejects_invalid_discovery_index(repo_root, tmp_path, mutation):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    index_path = root / "librarian-index.json"
    index = json.loads(index_path.read_text())
    if mutation == "missing":
        index_path.unlink()
    elif mutation == "duplicate_flat_name":
        index["entries"].append(dict(index["entries"][0]))
        index["count"] += 1
    elif mutation == "wrong_count":
        index["count"] += 1
    elif mutation == "extra_name":
        index["entries"][0]["flat_name"] = "extra-skill"
    elif mutation == "missing_name":
        index["entries"].pop()
        index["count"] -= 1
    elif mutation == "malformed_entry":
        index["entries"][0]["description"] = ""
    elif mutation == "skill_id_mismatch":
        index["entries"][0]["skill_id"] = "asr_0000000000000000"
    else:
        raise AssertionError(mutation)
    if index_path.exists():
        write_json(index_path, index)

    assert "registry.discovery-index" in check_ids(root)


def test_discovery_index_includes_active_skills_and_quarantine_load_names(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    skills_path = root / "registry/skills.json"
    skills = json.loads(skills_path.read_text())
    ordinary_skill = skills["skills"][0]
    ordinary_skill["state"] = "deprecated"
    write_json(skills_path, skills)

    index_path = root / "librarian-index.json"
    index = json.loads(index_path.read_text())
    index["entries"] = [
        entry for entry in index["entries"]
        if entry["flat_name"] != ordinary_skill["load_name"]
    ]
    quarantine_path = root / "registry/quarantine.json"
    quarantine = json.loads(quarantine_path.read_text())
    quarantine_record = quarantine["records"][0]
    quarantine_record["load_name"] = "quarantined-skill"
    write_json(quarantine_path, quarantine)
    quarantine_entry = dict(index["entries"][0])
    quarantine_entry.update({
        "flat_name": quarantine_record["load_name"],
        "skill_id": quarantine_record["skill_id"],
    })
    index["entries"].append(quarantine_entry)
    index["count"] = len(index["entries"])
    write_json(index_path, index)

    assert "registry.discovery-index" not in check_ids(root)

    index["entries"] = [
        entry for entry in index["entries"]
        if entry["flat_name"] != quarantine_record["load_name"]
    ]
    index["count"] = len(index["entries"])
    write_json(index_path, index)

    assert "registry.discovery-index" in check_ids(root)


def test_verify_ignores_non_authoritative_discovery_metadata(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    copy_librarian_index(repo_root, root)
    index_path = root / "librarian-index.json"
    index = json.loads(index_path.read_text())
    index["entries"][0].update({
        "risk": "dangerous",
        "content_hash": "0" * 64,
        "license": "untrusted",
    })
    write_json(index_path, index)

    assert "registry.discovery-index" not in check_ids(root)


def test_missing_skill_marker_fails(tmp_path):
    (tmp_path / "registry").mkdir()
    (tmp_path / "catalog/x/y/z").mkdir(parents=True)
    (tmp_path / "registry/skills.json").write_text(json.dumps({"skills": [{
        "skill_id": "asr_0123456789abcdef", "name": "z", "load_name": "z",
        "catalog_path": "catalog/x/y/z", "content_sha256": "0" * 64,
    }]}))
    report = verify_repository(tmp_path)
    assert report.result == "fail"
    assert any(finding["check_id"] == "catalog.skill-root" for finding in report.findings)


def test_strict_contract_rejects_cross_registry_conflicts(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    skills = json.loads((root / "registry/skills.json").read_text())
    quarantine = json.loads((root / "registry/quarantine.json").read_text())
    duplicate = skills["skills"][0]
    quarantine["records"][0]["skill_id"] = duplicate["skill_id"]
    (root / "registry/quarantine.json").write_text(json.dumps(quarantine))
    (root / "registry/aliases.json").write_text(json.dumps({"schema_version": 1, "aliases": [{"alias": duplicate["load_name"], "target_skill_id": duplicate["skill_id"]}]}))
    check_ids = {finding["check_id"] for finding in verify_repository(root).findings}
    assert {"registry.identity-overlap", "registry.alias-shadow"} <= check_ids


def test_strict_contract_rejects_bad_frontmatter_and_source_lock(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    skills = json.loads((root / "registry/skills.json").read_text())["skills"]
    (root / skills[0]["catalog_path"] / "SKILL.md").write_text("---\nname: wrong\ndescription: ''\n---\n")
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"][0]["commit"] = "main"
    (root / "registry/sources.lock.json").write_text(json.dumps(lock))
    check_ids = {finding["check_id"] for finding in verify_repository(root).findings}
    assert {"catalog.frontmatter", "registry.source-lock"} <= check_ids


def test_strict_contract_rejects_expired_exception(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    (root / "registry/exceptions.json").write_text(json.dumps({"schema_version": 1, "exceptions": [{
        "exception_id": "EX-001", "requirement_ids": ["GR-04"], "owner": "hbui290",
        "rationale": "fixture", "created_at": "2026-01-01", "expires_at": "2026-01-02",
    }]}))
    assert any(finding["check_id"] == "governance.exception" for finding in verify_repository(root).findings)


def test_strict_contract_rejects_unknown_schema_version(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    (root / "registry/schema-version.json").write_text(json.dumps({"schema_version": 999}))
    assert any(finding["check_id"] == "registry.schema-version" for finding in verify_repository(root).findings)


def test_strict_contract_rejects_core_record_that_is_not_safe(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    record = json.loads((root / "registry/skills.json").read_text())["skills"][0]
    (root / "registry/core.json").write_text(json.dumps({"schema_version": 1, "skill_ids": [record["skill_id"]]}))
    check_ids = {finding["check_id"] for finding in verify_repository(root).findings}
    assert "registry.core" in check_ids


def test_strict_contract_rejects_malformed_core_members(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    (root / "registry/core.json").write_text(json.dumps({"schema_version": 1, "skill_ids": [[]]}))
    check_ids = {finding["check_id"] for finding in verify_repository(root).findings}
    assert "registry.core" in check_ids


def test_strict_contract_rejects_invalid_upstream_review(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/upstream-review.json").read_text())
    payload["records"] = [
        {
            "source_path": "skills/example",
            "change": "added",
            "disposition": "accepted",
            "reason": "Invalid disposition fixture",
        }
    ]
    (root / "registry/upstream-review.json").write_text(json.dumps(payload))
    check_ids = {finding["check_id"] for finding in verify_repository(root).findings}
    assert "registry.upstream-review" in check_ids


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_artifact",
        "digest_mismatch",
        "duplicate_path",
        "wrong_source_id",
        "wrong_source_commit",
        "wrong_count",
        "pending_decision",
        "empty_reason",
    ],
)
def test_verify_rejects_invalid_reviewed_source_evidence(
    repo_root, tmp_path, mutation
):
    root = clone_repository_fixture(repo_root, tmp_path)
    source, artifact_path, artifact = reviewed_source_artifact(root)
    if mutation == "missing_artifact":
        artifact_path.unlink()
    elif mutation == "digest_mismatch":
        source["review"]["manifest_sha256"] = "c" * 64
    elif mutation == "duplicate_path":
        artifact["decisions"].append(dict(artifact["decisions"][0]))
        artifact["candidate_count"] = 2
    elif mutation == "wrong_source_id":
        artifact["source_id"] = "other-source"
    elif mutation == "wrong_source_commit":
        artifact["source_commit"] = "c" * 40
    elif mutation == "wrong_count":
        artifact["candidate_count"] = 2
    elif mutation == "pending_decision":
        artifact["decisions"][0]["decision"] = "pending"
    elif mutation == "empty_reason":
        artifact["decisions"][0]["reason"] = ""
    else:
        raise AssertionError(mutation)
    if artifact_path.exists():
        write_json(artifact_path, artifact)
    lock_path = root / "registry/sources.lock.json"
    lock = json.loads(lock_path.read_text())
    reviewed = next(
        item
        for item in lock["sources"]
        if item["source_id"] == source["source_id"]
    )
    reviewed["review"] = source["review"]
    write_json(lock_path, lock)

    assert "registry.source-review" in check_ids(root)


def test_verify_rejects_review_artifact_directory_symlink(
    repo_root, tmp_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    _, artifact_path, artifact = reviewed_source_artifact(root)
    external = tmp_path.parent / f"{tmp_path.name}-external-reviews"
    external.mkdir()
    write_json(external / artifact_path.name, artifact)
    shutil.rmtree(artifact_path.parent)
    artifact_path.parent.symlink_to(external, target_is_directory=True)

    assert "registry.source-review" in check_ids(root)


def test_verify_rejects_review_artifact_symlink(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    _, artifact_path, artifact = reviewed_source_artifact(root)
    external = tmp_path.parent / f"{tmp_path.name}-external-artifact.json"
    write_json(external, artifact)
    artifact_path.unlink()
    artifact_path.symlink_to(external)

    assert "registry.source-review" in check_ids(root)


@pytest.mark.parametrize(
    "source_path",
    ["", ".", "..", "/skills/azure-blob-storage", "skills//azure", "skills/./azure", "skills/../azure", "skills\\azure"],
)
def test_verify_rejects_malformed_review_decision_source_path(
    repo_root, tmp_path, source_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    _, artifact_path, artifact = reviewed_source_artifact(root)
    artifact["decisions"][0]["source_path"] = source_path
    write_json(artifact_path, artifact)

    assert "registry.source-review" in check_ids(root)


def test_verify_rejects_non_utf8_review_artifact(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    _, artifact_path, _ = reviewed_source_artifact(root)
    artifact_path.write_bytes(b"\xff")

    report = verify_repository(root)
    assert report.result == "fail"
    assert "registry.source-review" in {
        finding["check_id"] for finding in report.findings
    }


def test_default_core_contains_only_audited_safe_skill(repo_root):
    core = json.loads((repo_root / "registry/core.json").read_text())["skill_ids"]
    skills = {record["skill_id"]: record for record in json.loads((repo_root / "registry/skills.json").read_text())["skills"]}
    assert core == ["asr_8b273fe4fe068d88"]
    assert skills[core[0]]["risk"] == "safe"
    assert "core-audit" in skills[core[0]]["risk_reasons"]


def test_verify_rejects_record_commit_not_equal_to_source_lock(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/skills.json").read_text())
    payload["skills"][0]["source_commit"] = "b" * 40
    write_json(root / "registry/skills.json", payload)
    assert "registry.provenance" in check_ids(root)


def test_verify_rejects_skill_id_not_derived_from_source(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/skills.json").read_text())
    payload["skills"][0]["skill_id"] = "asr_0000000000000000"
    write_json(root / "registry/skills.json", payload)
    assert "registry.identity" in check_ids(root)


def test_verify_rejects_duplicate_source_path_across_active_and_quarantine(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    active = json.loads((root / "registry/skills.json").read_text())["skills"][0]
    quarantine = json.loads((root / "registry/quarantine.json").read_text())
    quarantine["records"].append({**active, "disposition": "quarantined"})
    write_json(root / "registry/quarantine.json", quarantine)
    assert "registry.source-path" in check_ids(root)


def test_verify_rejects_invalid_source_lifecycle(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"][0].update({"status": "retired", "refreshable": True})
    write_json(root / "registry/sources.lock.json", lock)
    assert "registry.source-lock" in check_ids(root)


def test_verify_rejects_boolean_source_timeout(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"][0]["timeout_seconds"] = True
    write_json(root / "registry/sources.lock.json", lock)
    assert "registry.source-lock" in check_ids(root)


def test_verify_rejects_non_refreshable_active_source(repo_root, tmp_path):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"][1]["refreshable"] = False
    write_json(root / "registry/sources.lock.json", lock)
    assert "registry.source-lock" in check_ids(root)


@pytest.mark.parametrize(
    "case",
    [
        "extra_top_level_field",
        "extra_record_field",
        "missing_metadata_index",
        "invalid_source_id",
        "noncanonical_github_url",
        "github_url_without_dot_git",
        "unsupported_layout",
        "invalid_layout_type",
        "unsafe_skills_root",
        "glob_skills_root",
        "invalid_metadata_index_type",
        "invalid_status_type",
        "missing_review",
        "extra_review_field",
    ],
)
def test_verify_rejects_malformed_source_lock_schema(
    repo_root, tmp_path, case
):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    source = lock["sources"][1]
    if case == "extra_top_level_field":
        lock["unexpected"] = True
    elif case == "extra_record_field":
        source["unexpected"] = True
    elif case == "missing_metadata_index":
        del source["metadata_index"]
    elif case == "invalid_source_id":
        source["source_id"] = "Bad_ID"
    elif case == "noncanonical_github_url":
        source["url"] = "https://gitlab.com/example/skills.git"
    elif case == "github_url_without_dot_git":
        source["url"] = "https://github.com/example/skills"
    elif case == "unsupported_layout":
        source["layout"] = "monorepo"
    elif case == "invalid_layout_type":
        source["layout"] = []
    elif case == "unsafe_skills_root":
        source["skills_root"] = "../skills"
    elif case == "glob_skills_root":
        source["skills_root"] = "skills/*"
    elif case == "invalid_metadata_index_type":
        source["metadata_index"] = 1
    elif case == "invalid_status_type":
        source["status"] = []
    elif case == "missing_review":
        del source["review"]
    elif case == "extra_review_field":
        source["review"]["unexpected"] = True
    else:
        raise AssertionError(case)
    write_json(root / "registry/sources.lock.json", lock)

    assert "registry.source-lock" in check_ids(root)


def test_verify_requires_each_record_to_join_exactly_one_locked_source(
    repo_root, tmp_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"].append(dict(lock["sources"][1]))
    write_json(root / "registry/sources.lock.json", lock)

    ids = check_ids(root)

    assert "registry.source-lock" in ids
    assert "registry.provenance" in ids


def test_verify_reports_unhashable_source_id_as_invalid_source_lock(
    repo_root, tmp_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    lock = json.loads((root / "registry/sources.lock.json").read_text())
    lock["sources"][0]["source_id"] = []
    write_json(root / "registry/sources.lock.json", lock)

    assert "registry.source-lock" in check_ids(root)


def test_verify_reports_unhashable_record_source_id_as_invalid_provenance(
    repo_root, tmp_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/skills.json").read_text())
    payload["skills"][0]["source_id"] = []
    write_json(root / "registry/skills.json", payload)

    assert "registry.provenance" in check_ids(root)


def test_verify_rejects_canonical_target_that_is_itself_canonical(
    repo_root, tmp_path
):
    root = clone_repository_fixture(repo_root, tmp_path)
    payload = json.loads((root / "registry/skills.json").read_text())
    first, second, third = payload["skills"][:3]
    first["canonical_skill_id"] = second["skill_id"]
    second["canonical_skill_id"] = third["skill_id"]
    write_json(root / "registry/skills.json", payload)

    assert "registry.canonical-target" in check_ids(root)


@pytest.mark.parametrize("sources", [None, 1, {}, "invalid"])
def test_verify_reports_malformed_source_collection(repo_root, tmp_path, sources):
    root = clone_repository_fixture(repo_root, tmp_path)
    write_json(
        root / "registry/sources.lock.json",
        {"schema_version": 1, "sources": sources},
    )
    assert "registry.source-lock" in check_ids(root)


def test_quarantine_skill_ids_are_derived_from_source(repo_root):
    records = json.loads((repo_root / "registry/quarantine.json").read_text())["records"]
    assert all(
        record["skill_id"] == stable_skill_id(record["source_id"], record["source_path"])
        for record in records
    )
