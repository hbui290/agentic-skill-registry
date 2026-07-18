import json
from pathlib import Path

from skill_registry.hashing import UnsafeCatalogPath, tree_sha256
from skill_registry.text import tokenize


class RegistryRuntimeError(RuntimeError):
    pass


class SkillConfirmationRequired(RegistryRuntimeError):
    def __init__(self, payload: dict[str, object]) -> None:
        skill = payload["skill"]
        super().__init__(
            f"confirmation required for {skill['risk']} skill: {skill['load_name']}"
        )
        self.payload = payload


class SkillBlocked(RegistryRuntimeError):
    pass


def _load_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryRuntimeError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise RegistryRuntimeError(f"expected object: {path}")
    return value


def _score(query: set[str], record: dict[str, object], metadata: dict[str, object]) -> int:
    names = tokenize(f"{record['name']} {record['load_name']}")
    taxonomy = tokenize(metadata.get("taxonomy", ""))
    category = tokenize(metadata.get("category_fine", ""))
    description = tokenize(metadata.get("description", ""))
    return sum(
        8 * (term in names)
        + 4 * (term in taxonomy)
        + 3 * (term in category)
        + 8 * (term in description)
        for term in query
    )


def search_skills(root: Path, query: str, limit: int = 10) -> dict[str, object]:
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    query_tokens = tokenize(query)
    if not query_tokens:
        raise ValueError("query must contain at least one letter or number")

    skills = _load_object(root / "registry" / "skills.json").get("skills", [])
    entries = _load_object(root / "librarian-index.json").get("entries", [])
    core = set(_load_object(root / "registry" / "core.json").get("skill_ids", []))
    if not isinstance(skills, list) or not isinstance(entries, list):
        raise RegistryRuntimeError("invalid registry or librarian index")

    metadata_by_name: dict[str, dict[str, object]] = {}
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("flat_name"), str):
            continue
        load_name = item["flat_name"]
        if load_name in metadata_by_name:
            raise RegistryRuntimeError(f"duplicate discovery metadata: {load_name}")
        metadata_by_name[load_name] = item

    matches: list[dict[str, object]] = []
    for record in skills:
        if (
            not isinstance(record, dict)
            or record.get("state") != "active"
            or record.get("canonical_skill_id")
            or record.get("risk") == "dangerous"
        ):
            continue
        load_name = str(record.get("load_name", ""))
        metadata = metadata_by_name.get(load_name)
        if metadata is None:
            raise RegistryRuntimeError(f"missing discovery metadata: {load_name}")
        score = _score(query_tokens, record, metadata)
        if score == 0:
            continue
        if record.get("risk") == "safe":
            score += 1
        if record.get("skill_id") in core:
            score += 2
        matches.append(
            {
                "skill_id": record["skill_id"],
                "name": record["name"],
                "load_name": load_name,
                "taxonomy": metadata.get("taxonomy", ""),
                "category": metadata.get("category_fine", ""),
                "description": metadata.get("description", ""),
                "risk": record["risk"],
                "risk_reasons": record["risk_reasons"],
                "core": record["skill_id"] in core,
                "score": score,
            }
        )
    matches.sort(key=lambda item: (-int(item["score"]), str(item["load_name"])))
    return {"query": query, "matches": matches[:limit]}


def _records(root: Path, filename: str, key: str) -> list[dict[str, object]]:
    value = _load_object(root / "registry" / filename).get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RegistryRuntimeError(f"invalid registry/{filename}")
    return value


def _skill_metadata(record: dict[str, object], core: set[str]) -> dict[str, object]:
    return {
        "skill_id": record["skill_id"],
        "load_name": record["load_name"],
        "risk": record["risk"],
        "risk_reasons": record["risk_reasons"],
        "core": record["skill_id"] in core,
        "source_id": record["source_id"],
        "source_commit": record["source_commit"],
        "source_path": record["source_path"],
        "license": record["license"],
        "content_sha256": record["content_sha256"],
    }


def read_skill(root: Path, identifier: str, allow_unreviewed: bool = False) -> dict[str, object]:
    skills = _records(root, "skills.json", "skills")
    quarantine = _records(root, "quarantine.json", "records")
    core = set(_load_object(root / "registry" / "core.json").get("skill_ids", []))

    if any(
        identifier in {str(item.get("skill_id", "")), str(item.get("name", ""))}
        for item in quarantine
    ):
        raise SkillBlocked(f"quarantined skill: {identifier}")
    matches = [
        item
        for item in skills
        if identifier in {str(item.get("skill_id", "")), str(item.get("load_name", ""))}
    ]
    if len(matches) != 1:
        raise SkillBlocked(f"skill not found or ambiguous: {identifier}")
    record = matches[0]
    if record.get("state") != "active":
        raise SkillBlocked(f"skill is not active: {identifier}")
    risk = str(record.get("risk", ""))
    if risk == "dangerous":
        raise SkillBlocked(f"dangerous skill blocked: {identifier}")
    if risk not in {"safe", "unknown", "review"}:
        raise SkillBlocked(f"unsupported risk state: {risk}")

    catalog = (root / "catalog").resolve()
    path = (root / str(record.get("catalog_path", ""))).resolve()
    if not path.is_relative_to(catalog):
        raise SkillBlocked(f"skill path outside catalog: {identifier}")
    marker = path / "SKILL.md"
    if not marker.is_file():
        raise SkillBlocked(f"SKILL.md missing: {identifier}")
    try:
        observed = tree_sha256(path)
    except (OSError, UnsafeCatalogPath) as error:
        raise SkillBlocked(f"unsafe skill tree: {error}") from error
    if observed != record.get("content_sha256"):
        raise SkillBlocked(f"hash mismatch: {identifier}")
    if risk in {"unknown", "review"} and not allow_unreviewed:
        raise SkillConfirmationRequired(
            {"error": "confirmation_required", "skill": _skill_metadata(record, core)}
        )

    return {
        "skill": _skill_metadata(record, core),
        "instructions": marker.read_text(encoding="utf-8"),
    }
