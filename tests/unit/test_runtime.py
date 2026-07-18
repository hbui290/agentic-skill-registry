import json
from pathlib import Path

import pytest

from skill_registry.hashing import tree_sha256
from skill_registry.runtime import (
    RegistryRuntimeError,
    SkillBlocked,
    SkillConfirmationRequired,
    read_skill,
    search_skills,
)


def build_registry(root: Path, specs: list[dict[str, object]]) -> list[dict[str, object]]:
    (root / "registry").mkdir()
    records = []
    entries = []
    core_ids = []
    for number, spec in enumerate(specs, start=1):
        name = str(spec["name"])
        taxonomy = str(spec.get("taxonomy", "engineering/testing"))
        description = str(spec.get("description", name))
        skill_id = f"asr_{number:016x}"
        skill_root = root / "catalog" / taxonomy / name
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\nUse {name}.\n",
            encoding="utf-8",
        )
        record = {
            "skill_id": skill_id,
            "name": name,
            "load_name": name,
            "catalog_path": skill_root.relative_to(root).as_posix(),
            "source_id": "fixture",
            "source_commit": "a" * 40,
            "source_path": f"skills/{name}",
            "content_sha256": tree_sha256(skill_root),
            "license": "MIT",
            "risk": str(spec.get("risk", "unknown")),
            "risk_reasons": ["fixture"],
            "state": str(spec.get("state", "active")),
            "canonical_skill_id": spec.get("canonical_skill_id"),
            "first_seen_version": "1.0.0",
        }
        records.append(record)
        entries.append(
            {
                "name": name,
                "flat_name": name,
                "taxonomy": taxonomy,
                "category_fine": str(spec.get("category", "engineering")),
                "description": description,
            }
        )
        if spec.get("core"):
            core_ids.append(skill_id)
    (root / "registry" / "skills.json").write_text(
        json.dumps({"schema_version": 1, "skills": records}), encoding="utf-8"
    )
    (root / "registry" / "core.json").write_text(
        json.dumps({"schema_version": 1, "skill_ids": core_ids}), encoding="utf-8"
    )
    (root / "registry" / "quarantine.json").write_text(
        json.dumps({"schema_version": 1, "records": []}), encoding="utf-8"
    )
    (root / "librarian-index.json").write_text(
        json.dumps({"schemaVersion": 1, "entries": entries}), encoding="utf-8"
    )
    return records


def test_search_ranks_name_and_taxonomy_before_description(tmp_path):
    build_registry(
        tmp_path,
        [
            {
                "name": "security-audit",
                "taxonomy": "security/auditing",
                "description": "Review a repository.",
            },
            {
                "name": "release-notes",
                "taxonomy": "writing/documentation",
                "description": "Mention security audit results.",
            },
        ],
    )
    matches = search_skills(tmp_path, "security audit")["matches"]
    assert [item["load_name"] for item in matches] == ["security-audit", "release-notes"]
    assert [(item["load_name"], item["score"]) for item in matches] == [
        ("security-audit", 20),
        ("release-notes", 16),
    ]


def test_search_adds_safety_bonus_only_after_text_match(tmp_path):
    build_registry(
        tmp_path,
        [
            {"name": "pdf", "description": "Work with PDF documents."},
            {
                "name": "unrelated-safe",
                "description": "Manage calendars.",
                "risk": "safe",
                "core": True,
            },
        ],
    )
    matches = search_skills(tmp_path, "pdf")["matches"]
    assert [item["load_name"] for item in matches] == ["pdf"]


def test_search_excludes_dangerous_inactive_and_canonical_records(tmp_path):
    build_registry(
        tmp_path,
        [
            {"name": "safe-audit", "description": "Security audit", "risk": "safe"},
            {"name": "danger-audit", "description": "Security audit", "risk": "dangerous"},
            {"name": "old-audit", "description": "Security audit", "state": "deprecated"},
            {
                "name": "alias-audit",
                "description": "Security audit",
                "canonical_skill_id": "asr_0000000000000001",
            },
        ],
    )
    matches = search_skills(tmp_path, "security audit")["matches"]
    assert [item["load_name"] for item in matches] == ["safe-audit"]


def test_search_rejects_missing_or_duplicate_discovery_metadata(tmp_path):
    records = build_registry(tmp_path, [{"name": "security-audit"}])
    index = tmp_path / "librarian-index.json"
    index.write_text(json.dumps({"schemaVersion": 1, "entries": []}), encoding="utf-8")
    with pytest.raises(RegistryRuntimeError, match=records[0]["load_name"]):
        search_skills(tmp_path, "security")

    duplicate = {
        "name": "security-audit",
        "flat_name": "security-audit",
        "taxonomy": "security/auditing",
        "category_fine": "security",
        "description": "duplicate",
    }
    index.write_text(
        json.dumps({"schemaVersion": 1, "entries": [duplicate, duplicate]}), encoding="utf-8"
    )
    with pytest.raises(RegistryRuntimeError, match="duplicate discovery metadata"):
        search_skills(tmp_path, "security")


@pytest.mark.parametrize(("query", "limit"), [("", 10), ("---", 10), ("pdf", 0), ("pdf", 51)])
def test_search_rejects_invalid_query_or_limit(tmp_path, query, limit):
    build_registry(tmp_path, [{"name": "pdf"}])
    with pytest.raises(ValueError):
        search_skills(tmp_path, query, limit=limit)


def test_search_ties_are_sorted_by_load_name(tmp_path):
    build_registry(
        tmp_path,
        [
            {"name": "zeta", "description": "shared"},
            {"name": "alpha", "description": "shared"},
        ],
    )
    matches = search_skills(tmp_path, "shared")["matches"]
    assert [item["load_name"] for item in matches] == ["alpha", "zeta"]


def test_read_allows_safe_skill_and_returns_only_instructions(tmp_path):
    record = build_registry(tmp_path, [{"name": "safe-doc", "risk": "safe", "core": True}])[0]
    result = read_skill(tmp_path, record["load_name"])
    assert result["skill"]["skill_id"] == record["skill_id"]
    assert result["skill"]["core"] is True
    assert result["instructions"].startswith("---\nname: safe-doc")


def test_unknown_read_returns_structured_confirmation(tmp_path):
    record = build_registry(tmp_path, [{"name": "unknown-skill", "risk": "unknown"}])[0]
    with pytest.raises(SkillConfirmationRequired) as caught:
        read_skill(tmp_path, "unknown-skill")

    assert caught.value.payload == {
        "error": "confirmation_required",
        "skill": {
            "skill_id": record["skill_id"],
            "load_name": "unknown-skill",
            "risk": "unknown",
            "risk_reasons": ["fixture"],
            "core": False,
            "source_id": "fixture",
            "source_commit": "a" * 40,
            "source_path": "skills/unknown-skill",
            "license": "MIT",
            "content_sha256": record["content_sha256"],
        },
    }
    assert "instructions" not in caught.value.payload


def test_safe_read_exposes_integrity_metadata(tmp_path):
    record = build_registry(tmp_path, [{"name": "safe-skill", "risk": "safe"}])[0]
    result = read_skill(tmp_path, "safe-skill")
    assert result["skill"]["source_path"] == record["source_path"]
    assert result["skill"]["content_sha256"] == record["content_sha256"]


@pytest.mark.parametrize("risk", ["unknown", "review"])
def test_read_requires_confirmation_for_unreviewed_skill(tmp_path, risk):
    record = build_registry(tmp_path, [{"name": "unreviewed", "risk": risk}])[0]
    with pytest.raises(SkillConfirmationRequired, match=risk):
        read_skill(tmp_path, record["skill_id"])
    result = read_skill(tmp_path, record["skill_id"], allow_unreviewed=True)
    assert result["skill"]["risk"] == risk


def test_read_blocks_dangerous_even_with_override(tmp_path):
    record = build_registry(tmp_path, [{"name": "danger", "risk": "dangerous"}])[0]
    with pytest.raises(SkillBlocked, match="dangerous"):
        read_skill(tmp_path, record["skill_id"], allow_unreviewed=True)


def test_read_blocks_quarantined_inactive_and_missing_skills(tmp_path):
    inactive = build_registry(tmp_path, [{"name": "inactive", "risk": "safe", "state": "deprecated"}])[0]
    quarantine = {"skill_id": "asr_ffffffffffffffff", "name": "blocked", "disposition": "quarantined"}
    (tmp_path / "registry" / "quarantine.json").write_text(
        json.dumps({"schema_version": 1, "records": [quarantine]}), encoding="utf-8"
    )
    with pytest.raises(SkillBlocked, match="quarantined"):
        read_skill(tmp_path, quarantine["skill_id"])
    with pytest.raises(SkillBlocked, match="not active"):
        read_skill(tmp_path, inactive["skill_id"])
    with pytest.raises(SkillBlocked, match="not found"):
        read_skill(tmp_path, "missing")


def test_read_blocks_catalog_escape(tmp_path):
    record = build_registry(tmp_path, [{"name": "candidate", "risk": "safe"}])[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: candidate\ndescription: escaped\n---\n")
    record["catalog_path"] = "outside"
    record["content_sha256"] = tree_sha256(outside)
    (tmp_path / "registry" / "skills.json").write_text(
        json.dumps({"schema_version": 1, "skills": [record]}), encoding="utf-8"
    )
    with pytest.raises(SkillBlocked, match="outside catalog"):
        read_skill(tmp_path, record["skill_id"])


def test_read_blocks_symlink_and_hash_mismatch(tmp_path):
    record = build_registry(tmp_path, [{"name": "candidate", "risk": "safe"}])[0]
    skill_root = tmp_path / record["catalog_path"]
    (skill_root / "linked").symlink_to(tmp_path / "outside")
    with pytest.raises(SkillBlocked, match="unsafe skill tree"):
        read_skill(tmp_path, record["skill_id"])

    (skill_root / "linked").unlink()
    (skill_root / "SKILL.md").write_text(
        (skill_root / "SKILL.md").read_text(encoding="utf-8") + "modified\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillBlocked, match="hash mismatch"):
        read_skill(tmp_path, record["skill_id"])


def test_unknown_hash_mismatch_blocks_before_confirmation(tmp_path):
    record = build_registry(tmp_path, [{"name": "candidate", "risk": "unknown"}])[0]
    skill_root = tmp_path / record["catalog_path"]
    (skill_root / "SKILL.md").write_text("modified\n", encoding="utf-8")

    with pytest.raises(SkillBlocked, match="hash mismatch"):
        read_skill(tmp_path, record["skill_id"])


def test_read_never_executes_bundled_scripts(tmp_path):
    record = build_registry(tmp_path, [{"name": "safe-doc", "risk": "safe"}])[0]
    skill_root = tmp_path / record["catalog_path"]
    sentinel = tmp_path / "executed"
    script = skill_root / "run.sh"
    script.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n", encoding="utf-8")
    script.chmod(0o755)
    record["content_sha256"] = tree_sha256(skill_root)
    (tmp_path / "registry" / "skills.json").write_text(
        json.dumps({"schema_version": 1, "skills": [record]}), encoding="utf-8"
    )
    read_skill(tmp_path, record["skill_id"])
    assert not sentinel.exists()
