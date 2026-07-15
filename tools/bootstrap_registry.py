import json
import re
from datetime import date
from pathlib import Path

import yaml

from skill_registry.collector import discover_catalog
from skill_registry.filesystem import dump_json
from skill_registry.hashing import tree_sha256
from skill_registry.identity import assign_load_names, stable_skill_id


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_COMMIT = "82c86e65677aa1b40fa8207f95bc43766494a3db"
LEGACY_COMMIT = "a3f3ac3bb434884b9847cf6df43a534ec00a6d71"
LEGACY_NAMES = {"SPDD", "docx", "pdf", "pptx", "xlsx", "linear"}


def frontmatter(path: Path) -> dict[str, object]:
    match = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8", errors="replace"), re.DOTALL)
    if not match:
        return {}
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    paths = discover_catalog(ROOT)
    relative = [path.relative_to(ROOT).as_posix() for path in paths]
    load_names = assign_load_names(relative)
    legacy_index = json.loads((ROOT / "librarian-index.json").read_text())["entries"]
    metadata_by_path = {f"catalog/{entry['taxonomy']}/{entry['name']}": entry for entry in legacy_index}
    skills: list[dict[str, object]] = []
    quarantine: list[dict[str, object]] = []
    for path, rel in zip(paths, relative, strict=True):
        directory_name = path.name
        source_id = "legacy-local" if directory_name in LEGACY_NAMES else "sickn33-agentic-awesome-skills"
        source_commit = LEGACY_COMMIT if source_id == "legacy-local" else UPSTREAM_COMMIT
        source_path = rel.removeprefix("catalog/") if source_id == "legacy-local" else f"skills/{directory_name}"
        skill_id = stable_skill_id(source_id, source_path)
        digest = tree_sha256(path)
        marker = path / "SKILL.md"
        if not marker.is_file():
            quarantine.append({"skill_id": skill_id, "name": directory_name, "catalog_path": rel, "source_id": source_id, "source_commit": source_commit, "content_sha256": digest, "rule_ids": ["missing-skill-md"], "first_seen_date": date.today().isoformat(), "disposition": "quarantined"})
            continue
        meta = frontmatter(marker)
        skills.append({
            "skill_id": skill_id,
            "name": str(meta.get("name", "")).strip(),
            "load_name": load_names[rel],
            "catalog_path": rel,
            "source_id": source_id,
            "source_commit": source_commit,
            "source_path": source_path,
            "content_sha256": digest,
            "license": str(metadata_by_path.get(rel, {}).get("license", "unknown")),
            "risk": "unknown",
            "risk_reasons": ["initial-review-required"] if str(meta.get("description", "")).strip() else ["missing-description"],
            "state": "active",
            "canonical_skill_id": None,
            "first_seen_version": "1.0.0",
        })
    dump_json(ROOT / "registry/skills.json", {"schema_version": 1, "skills": sorted(skills, key=lambda item: item["skill_id"])})
    dump_json(ROOT / "registry/quarantine.json", {"schema_version": 1, "records": sorted(quarantine, key=lambda item: item["skill_id"])})
    dump_json(ROOT / "registry/aliases.json", {"schema_version": 1, "aliases": []})
    dump_json(ROOT / "registry/risk-overrides.json", {"schema_version": 1, "overrides": []})
    dump_json(ROOT / "registry/exceptions.json", {"schema_version": 1, "exceptions": []})
    return 0 if (len(skills), len(quarantine)) == (1952, 2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
