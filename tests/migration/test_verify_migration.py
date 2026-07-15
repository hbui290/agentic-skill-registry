import json
import subprocess
from pathlib import Path

import pytest

from tools.verify_migration import MigrationError, verify


def test_verify_reconciles_active_and_markerless_records(tmp_path: Path):
    valid = tmp_path / "catalog/engineering/testing/valid"
    valid.mkdir(parents=True)
    (valid / "SKILL.md").write_text("---\nname: valid\ndescription: valid\n---\n")
    markerless = tmp_path / "catalog/engineering/testing/bundle"
    markerless.mkdir(parents=True)
    (markerless / "plugin.json").write_text("{}")
    manifest = tmp_path / "registry/migration/legacy-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"entries": [
        "catalog/engineering/testing/valid",
        "catalog/engineering/testing/bundle",
    ]}))

    assert verify(tmp_path) == {"legacy": 2, "active_candidates": 1, "markerless": 1}


def test_verify_rejects_missing_path(tmp_path: Path):
    manifest = tmp_path / "registry/migration/legacy-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"entries": ["catalog/engineering/testing/missing"]}))

    with pytest.raises(MigrationError, match="missing legacy path"):
        verify(tmp_path)


def test_verify_rejects_content_changed_after_migration(tmp_path: Path):
    original = tmp_path / "engineering/testing/valid"
    original.mkdir(parents=True)
    (original / "SKILL.md").write_text("original")
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    migrated = tmp_path / "catalog/engineering/testing/valid"
    migrated.parent.mkdir(parents=True)
    original.rename(migrated)
    (migrated / "SKILL.md").write_text("changed")
    manifest = tmp_path / "registry/migration/legacy-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "source_commit": commit,
        "entries": ["catalog/engineering/testing/valid"],
    }))

    with pytest.raises(MigrationError, match="content changed"):
        verify(tmp_path)
