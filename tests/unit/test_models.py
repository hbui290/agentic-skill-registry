import json
from pathlib import Path

from skill_registry.filesystem import dump_json
from skill_registry.models import SkillRecord


def test_skill_record_serializes_with_sorted_reason_ids(tmp_path: Path):
    record = SkillRecord(
        skill_id="asr_0123456789abcdef",
        name="example",
        load_name="example",
        catalog_path="catalog/engineering/testing/example",
        source_id="legacy-local",
        source_commit="a3f3ac3bb434884b9847cf6df43a534ec00a6d71",
        source_path="engineering/testing/example",
        content_sha256="0" * 64,
        license="unknown",
        risk="unknown",
        risk_reasons=("missing-license", "contains-script"),
        state="active",
        canonical_skill_id=None,
        first_seen_version="1.0.0",
    )
    target = tmp_path / "skills.json"
    dump_json(target, {"schema_version": 1, "skills": [record.to_dict()]})
    payload = json.loads(target.read_text())
    assert payload["skills"][0]["risk_reasons"] == ["contains-script", "missing-license"]
    assert target.read_text().endswith("\n")
