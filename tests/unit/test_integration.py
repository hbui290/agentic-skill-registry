import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from skill_registry.integration import (
    IntegrationValidationError,
    build_librarian_integration_lock,
    verify_librarian_integration,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "integration_id": "codex-skill-librarian",
        "version": "1.0.0",
        "native_skill_path": "skills/skill-librarian/SKILL.md",
        "runtime": {
            "command": "skill-registry",
            "root_env": "AGENTIC_SKILL_REGISTRY_ROOT",
            "minimum_python": "3.11",
        },
        "process_dependency": "official-superpowers",
    }


def _root(tmp_path):
    (tmp_path / "registry").mkdir()
    skill = tmp_path / "skills/skill-librarian/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: skill-librarian\n---\n", encoding="utf-8")
    manifest = _manifest()
    (tmp_path / "registry/librarian-integration.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    lock = build_librarian_integration_lock(tmp_path)
    (tmp_path / "registry/librarian-integration.lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8"
    )
    return tmp_path


def _findings(root):
    findings: list[dict[str, object]] = []
    verify_librarian_integration(root, findings)
    return findings


def test_build_lock_is_deterministic_and_hashes_manifest_and_native_skill(tmp_path):
    root = _root(tmp_path)
    first = build_librarian_integration_lock(root)
    second = build_librarian_integration_lock(root)

    assert first == second
    assert first["manifest_sha256"] == hashlib.sha256(
        (root / "registry/librarian-integration.json").read_bytes()
    ).hexdigest()
    assert first["files"] == [{
        "path": "skills/skill-librarian/SKILL.md",
        "sha256": hashlib.sha256(
            (root / "skills/skill-librarian/SKILL.md").read_bytes()
        ).hexdigest(),
    }]


def test_valid_integration_passes(tmp_path):
    assert _findings(_root(tmp_path)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_manifest",
        "unexpected_manifest_field",
        "malformed_lock",
        "manifest_hash_mismatch",
        "skill_hash_mismatch",
        "unsafe_path",
    ],
)
def test_invalid_integration_is_reported(tmp_path, mutation):
    root = _root(tmp_path)
    manifest_path = root / "registry/librarian-integration.json"
    lock_path = root / "registry/librarian-integration.lock.json"
    if mutation == "missing_manifest":
        manifest_path.unlink()
    elif mutation == "unexpected_manifest_field":
        manifest = json.loads(manifest_path.read_text())
        manifest["extra"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "malformed_lock":
        lock_path.write_text("{}", encoding="utf-8")
    elif mutation == "manifest_hash_mismatch":
        lock = json.loads(lock_path.read_text())
        lock["manifest_sha256"] = "0" * 64
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
    elif mutation == "skill_hash_mismatch":
        (root / "skills/skill-librarian/SKILL.md").write_text(
            "changed", encoding="utf-8"
        )
    else:
        manifest = json.loads(manifest_path.read_text())
        manifest["native_skill_path"] = "../outside/SKILL.md"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    findings = _findings(root)

    assert [item["check_id"] for item in findings] == [
        "registry.librarian-integration"
    ]


def test_build_lock_rejects_native_skill_symlink(tmp_path):
    root = _root(tmp_path)
    target = root / "outside.md"
    target.write_text("outside", encoding="utf-8")
    skill = root / "skills/skill-librarian/SKILL.md"
    skill.unlink()
    skill.symlink_to(target)

    with pytest.raises(IntegrationValidationError, match="symlink"):
        build_librarian_integration_lock(root)


def test_generator_output_is_byte_stable(tmp_path):
    root = _root(tmp_path)
    script = Path(__file__).parents[2] / "tools/generate_librarian_integration_lock.py"
    command = [sys.executable, str(script), "--root", str(root)]

    subprocess.run(command, check=True)
    first = (root / "registry/librarian-integration.lock.json").read_bytes()
    subprocess.run(command, check=True)

    assert (root / "registry/librarian-integration.lock.json").read_bytes() == first
