import json
from pathlib import Path

from tools.migrate_v1 import migrate


def test_migrate_moves_macros_and_preserves_manifest_count(tmp_path: Path):
    skill = tmp_path / "engineering" / "testing" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\ndescription: test\n---\n")
    (tmp_path / ".antigravity-install-manifest.json").write_text(json.dumps({
        "schemaVersion": 1,
        "entries": ["engineering/testing/example"],
    }))

    entries = migrate(tmp_path)

    assert entries == ["catalog/engineering/testing/example"]
    assert (tmp_path / "catalog/engineering/testing/example/SKILL.md").is_file()
    assert not (tmp_path / "engineering").exists()
    saved = json.loads((tmp_path / "registry/migration/legacy-manifest.json").read_text())
    assert saved["entries"] == entries
