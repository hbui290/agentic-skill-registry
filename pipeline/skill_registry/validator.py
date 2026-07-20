import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from skill_registry.collector import discover_catalog
from skill_registry.hashing import UnsafeCatalogPath, tree_sha256
from skill_registry.identity import stable_skill_id
from skill_registry.integration import verify_librarian_integration
from skill_registry.reporting import VerificationReport


ID = re.compile(r"^asr_[0-9a-f]{16}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
GITHUB_URL = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$"
)
SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
SOURCE_LOCK_FIELDS = {
    "source_id",
    "url",
    "commit",
    "layout",
    "skills_root",
    "metadata_index",
    "license_note",
    "status",
    "refreshable",
    "timeout_seconds",
    "review",
}
LEGACY_SOURCE_REVIEW_FIELDS = {"status", "reason"}
REVIEWED_SOURCE_REVIEW_FIELDS = {
    "status",
    "artifact",
    "manifest_sha256",
}
SOURCE_REVIEW_ARTIFACT_FIELDS = {
    "schema_version",
    "source_id",
    "source_commit",
    "manifest_sha256",
    "candidate_count",
    "decisions",
}
SOURCE_REVIEW_DECISION_FIELDS = {
    "source_path",
    "content_sha256",
    "decision",
    "taxonomy",
    "category_fine",
    "canonical_skill_id",
    "reason",
}
SOURCE_LAYOUTS = {"legacy-snapshot", "skills-subdir"}
RISK_VALUES = {"safe", "review", "dangerous", "unknown"}
STATE_VALUES = {"active", "deprecated", "quarantined"}
SKILL_FIELDS = {"skill_id", "name", "load_name", "catalog_path", "source_id", "source_commit", "source_path", "content_sha256", "license", "risk", "risk_reasons", "state", "canonical_skill_id", "first_seen_version"}


def add(findings: list[dict[str, object]], check_id: str, requirement_ids: list[str], **context: object) -> None:
    findings.append({"check_id": check_id, "requirement_ids": requirement_ids, "result": "fail", **context})


def read_records(path: Path, key: str) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get(key, []) if isinstance(payload, dict) else []


def frontmatter(path: Path) -> dict[str, object]:
    match = re.match(r"^---\s*\n(.*?)\n---", path.read_text(encoding="utf-8", errors="replace"), re.DOTALL)
    if not match:
        return {}
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def valid_source_lock_record(source: object) -> bool:
    if not isinstance(source, dict) or set(source) != SOURCE_LOCK_FIELDS:
        return False
    source_id = source["source_id"]
    url = source["url"]
    commit = source["commit"]
    layout = source["layout"]
    skills_root = source["skills_root"]
    metadata_index = source["metadata_index"]
    license_note = source["license_note"]
    status = source["status"]
    refreshable = source["refreshable"]
    timeout = source["timeout_seconds"]
    review = source["review"]
    safe_skills_root = (
        isinstance(skills_root, str)
        and (
            skills_root == "" and layout == "legacy-snapshot"
            or (
                SAFE_RELATIVE_PATH.fullmatch(skills_root) is not None
                and all(part not in {".", ".."} for part in skills_root.split("/"))
            )
        )
    )
    return (
        isinstance(source_id, str)
        and SOURCE_ID.fullmatch(source_id) is not None
        and isinstance(url, str)
        and GITHUB_URL.fullmatch(url) is not None
        and isinstance(commit, str)
        and COMMIT.fullmatch(commit) is not None
        and isinstance(layout, str)
        and layout in SOURCE_LAYOUTS
        and safe_skills_root
        and (metadata_index is None or isinstance(metadata_index, str))
        and isinstance(license_note, str)
        and bool(license_note.strip())
        and isinstance(status, str)
        and status in {"active", "retired"}
        and isinstance(refreshable, bool)
        and isinstance(timeout, int)
        and not isinstance(timeout, bool)
        and 1 <= timeout <= 60
        and refreshable == (status == "active")
        and valid_source_review_record(source_id, commit, review)
    )


def valid_source_review_record(
    source_id: object, commit: object, review: object
) -> bool:
    if not isinstance(review, dict):
        return False
    if review.get("status") == "legacy":
        return (
            set(review) == LEGACY_SOURCE_REVIEW_FIELDS
            and isinstance(review.get("reason"), str)
            and bool(review["reason"].strip())
        )
    artifact = (
        "registry/source-reviews/"
        f"{source_id}-{commit}.json"
    )
    return (
        set(review) == REVIEWED_SOURCE_REVIEW_FIELDS
        and review.get("status") == "reviewed"
        and review.get("artifact") == artifact
        and isinstance(review.get("manifest_sha256"), str)
        and SHA.fullmatch(review["manifest_sha256"]) is not None
    )


def valid_normalized_relative_path(path: object) -> bool:
    return (
        isinstance(path, str)
        and SAFE_RELATIVE_PATH.fullmatch(path) is not None
        and all(part not in {".", ".."} for part in path.split("/"))
    )


def valid_discovery_entry(
    entry: object, records_by_load_name: dict[str, dict[str, object]]
) -> bool:
    if not isinstance(entry, dict):
        return False
    flat_name = entry.get("flat_name")
    return (
        isinstance(flat_name, str)
        and bool(flat_name.strip())
        and all(
            isinstance(entry.get(field), str) and bool(entry[field].strip())
            for field in ("taxonomy", "category_fine", "description")
        )
        and (
            "skill_id" not in entry
            or entry["skill_id"]
            == records_by_load_name.get(flat_name, {}).get("skill_id")
        )
    )


def valid_source_review_artifact(root: Path, source: dict[str, object]) -> bool:
    review = source["review"]
    if review["status"] == "legacy":
        return True
    try:
        repository_root = root.resolve(strict=True)
        artifact_path = root / str(review["artifact"])
        if artifact_path.is_symlink():
            return False
        artifact_path = artifact_path.resolve(strict=True)
        artifact_path.relative_to(repository_root)
        artifact = json.loads(
            artifact_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(artifact, dict) or set(artifact) != SOURCE_REVIEW_ARTIFACT_FIELDS:
        return False
    decisions = artifact.get("decisions")
    if (
        artifact.get("schema_version") != 1
        or artifact.get("source_id") != source["source_id"]
        or artifact.get("source_commit") != source["commit"]
        or artifact.get("manifest_sha256") != review["manifest_sha256"]
        or not isinstance(artifact.get("candidate_count"), int)
        or isinstance(artifact.get("candidate_count"), bool)
        or not isinstance(decisions, list)
        or artifact["candidate_count"] != len(decisions)
    ):
        return False
    paths: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != SOURCE_REVIEW_DECISION_FIELDS:
            return False
        source_path = decision.get("source_path")
        if (
            not valid_normalized_relative_path(source_path)
            or not isinstance(decision.get("content_sha256"), str)
            or SHA.fullmatch(decision["content_sha256"]) is None
            or decision.get("decision")
            not in {"import", "canonical", "quarantine", "reject"}
            or not isinstance(decision.get("reason"), str)
            or not decision["reason"].strip()
        ):
            return False
        paths.append(source_path)
    return len(paths) == len(set(paths))


def verify_repository(root: Path) -> VerificationReport:
    try:
        return _verify_repository(root)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        findings: list[dict[str, object]] = []
        add(findings, "registry.input", ["DR-08"], error=type(error).__name__)
        return VerificationReport("fail", 0, 1, 0, 0, tuple(findings))


def _verify_repository(root: Path) -> VerificationReport:
    findings: list[dict[str, object]] = []
    verify_librarian_integration(root, findings)
    registry = root / "registry"
    skills_path = registry / "skills.json"
    if not skills_path.is_file():
        add(findings, "registry.present", ["DR-08"])
        return VerificationReport("fail", 0, 1, 0, 0, tuple(findings))
    required = ("sources.lock.json", "aliases.json", "quarantine.json", "risk-overrides.json", "exceptions.json", "schema-version.json", "core.json", "upstream-review.json")
    missing = [name for name in required if not (registry / name).is_file()]
    if missing:
        add(findings, "registry.present", ["DR-08"], missing=missing)
    elif json.loads((registry / "schema-version.json").read_text(encoding="utf-8")).get("schema_version") != 1:
        add(findings, "registry.schema-version", ["DR-08"])
    index_path = root / "librarian-index.json"
    index_payload = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {}
    )
    skills = read_records(skills_path, "skills")
    quarantine = read_records(registry / "quarantine.json", "records")
    aliases = read_records(registry / "aliases.json", "aliases")
    exceptions = read_records(registry / "exceptions.json", "exceptions")
    core_payload = json.loads((registry / "core.json").read_text()) if (registry / "core.json").is_file() else {}
    core = core_payload.get("skill_ids") if isinstance(core_payload, dict) else None
    known_skills = {record.get("skill_id"): record for record in skills}
    if (
        core_payload.get("schema_version") != 1
        or not isinstance(core, list)
        or not all(isinstance(skill_id, str) for skill_id in core or [])
        or len(core) != len(set(core))
        or any(
            known_skills.get(skill_id, {}).get("state") != "active"
            or known_skills.get(skill_id, {}).get("risk") != "safe"
            for skill_id in core or []
        )
    ):
        add(findings, "registry.core", ["DR-08"])
    lock_payload = json.loads((registry / "sources.lock.json").read_text()) if (registry / "sources.lock.json").is_file() else {"sources": []}
    raw_sources = lock_payload.get("sources", []) if isinstance(lock_payload, dict) else []
    sources_valid = isinstance(raw_sources, list)
    sources = raw_sources if sources_valid else []
    source_by_id = {
        source["source_id"]: source
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    }
    source_id_counts = Counter(
        source["source_id"]
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    )
    source_ids = set(source_by_id)
    if (
        not sources_valid
        or not isinstance(lock_payload, dict)
        or set(lock_payload) != {"schema_version", "sources"}
        or lock_payload.get("schema_version") != 1
        or len(source_by_id) != len(sources)
        or any(not valid_source_lock_record(source) for source in sources)
    ):
        add(findings, "registry.source-lock", ["DR-04", "UR-01"])
    for source in sources:
        if isinstance(source, dict) and valid_source_lock_record(source):
            if not valid_source_review_artifact(root, source):
                add(
                    findings,
                    "registry.source-review",
                    ["DR-04", "UR-01"],
                    source_id=source["source_id"],
                )
    source_pair_counts = Counter(
        (record["source_id"], record["source_path"])
        for record in skills + quarantine
        if isinstance(record.get("source_id"), str)
        and isinstance(record.get("source_path"), str)
    )
    for record in skills + quarantine:
        source_id = record.get("source_id")
        source = source_by_id.get(source_id) if isinstance(source_id, str) else None
        source_path = record.get("source_path")
        if (
            source is None
            or source_id_counts[source_id] != 1
            or record.get("source_commit") != source.get("commit")
        ):
            add(findings, "registry.provenance", ["DR-04"], skill_id=record.get("skill_id"))
        if (
            not isinstance(source_id, str)
            or not isinstance(source_path, str)
            or source_path.startswith("/")
            or ".." in source_path.split("/")
            or source_pair_counts[(source_id, source_path)] > 1
        ):
            add(findings, "registry.source-path", ["DR-04"], skill_id=record.get("skill_id"))
        elif record.get("skill_id") != stable_skill_id(str(record.get("source_id")), source_path):
            add(findings, "registry.identity", ["DR-04"], skill_id=record.get("skill_id"))
    try:
        review_payload = json.loads((registry / "upstream-review.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        review_payload = {}
    review_records = review_payload.get("records") if isinstance(review_payload, dict) else None
    review_source = next((source for source in sources if isinstance(source, dict) and source.get("source_id") == review_payload.get("source_id")), None)
    review_valid = (
        isinstance(review_records, list)
        and review_payload.get("schema_version") == 1
        and isinstance(review_source, dict)
        and review_payload.get("pinned_commit") == review_source.get("commit")
        and COMMIT.fullmatch(str(review_payload.get("observed_commit", ""))) is not None
        and all(
            isinstance(record, dict)
            and isinstance(record.get("source_path"), str)
            and record["source_path"].startswith("skills/")
            and ".." not in record["source_path"].split("/")
            and record.get("change") in {"added", "modified"}
            and record.get("disposition") in {"review", "quarantined"}
            and isinstance(record.get("reason"), str)
            and bool(record["reason"].strip())
            for record in review_records or []
        )
    )
    if review_valid:
        review_paths = [record["source_path"] for record in review_records]
        review_valid = len(review_paths) == len(set(review_paths))
    if not review_valid:
        add(findings, "registry.upstream-review", ["UR-01"])
    ids: set[str] = set()
    load_names: set[str] = set()
    for record in skills:
        path = root / str(record.get("catalog_path", ""))
        marker = path / "SKILL.md"
        if not marker.is_file():
            add(findings, "catalog.skill-root", ["DR-03"], skill_id=record.get("skill_id"))
        missing_fields = sorted(SKILL_FIELDS - record.keys())
        if missing_fields:
            add(findings, "registry.skill-schema", ["DR-08"], missing=missing_fields)
            continue
        if not marker.is_file():
            continue
        metadata = frontmatter(marker)
        if metadata.get("name") != record["name"] or not str(metadata.get("description", "")).strip():
            add(findings, "catalog.frontmatter", ["DR-03"], skill_id=record["skill_id"])
        if not ID.fullmatch(str(record["skill_id"])) or record["skill_id"] in ids:
            add(findings, "registry.skill-id", ["DR-01"], skill_id=record["skill_id"])
        ids.add(record["skill_id"])
        if not record["load_name"] or record["load_name"] in load_names:
            add(findings, "registry.load-name", ["DR-02"], skill_id=record["skill_id"])
        load_names.add(record["load_name"])
        if (
            not isinstance(record["source_id"], str)
            or record["source_id"] not in source_ids
            or not COMMIT.fullmatch(str(record["source_commit"]))
            or not all(
                record.get(field)
                for field in ("source_path", "license", "risk", "risk_reasons", "state")
            )
        ):
            add(findings, "registry.provenance", ["DR-04", "DR-06"], skill_id=record["skill_id"])
        if record["risk"] not in RISK_VALUES or record["state"] not in STATE_VALUES:
            add(findings, "registry.state-values", ["DR-06"], skill_id=record["skill_id"])
        try:
            actual = tree_sha256(path)
        except (UnsafeCatalogPath, OSError):
            actual = ""
        if not SHA.fullmatch(str(record["content_sha256"])) or actual != record["content_sha256"]:
            add(findings, "catalog.content-hash", ["DR-05", "UR-06"], skill_id=record["skill_id"])
    quarantine_ids = {record.get("skill_id") for record in quarantine}
    if ids & quarantine_ids or len(quarantine_ids) != len(quarantine):
        add(findings, "registry.identity-overlap", ["DR-01", "DR-07"])
    for record in quarantine:
        if not ID.fullmatch(str(record.get("skill_id", ""))) or not record.get("rule_ids") or not record.get("disposition"):
            add(findings, "registry.quarantine", ["DR-07"], skill_id=record.get("skill_id"))
        path_value = record.get("catalog_path")
        if path_value:
            try:
                actual = tree_sha256(root / str(path_value))
            except (UnsafeCatalogPath, OSError):
                actual = ""
            if actual != record.get("content_sha256"):
                add(findings, "registry.quarantine-hash", ["DR-05", "DR-07"], skill_id=record.get("skill_id"))
    entries = index_payload.get("entries") if isinstance(index_payload, dict) else None
    discovery_records = [
        record for record in skills if record.get("state") == "active"
    ] + quarantine
    expected_names = {
        record["load_name"]
        for record in discovery_records
        if isinstance(record.get("load_name"), str)
    }
    records_by_load_name = {
        record["load_name"]: record
        for record in discovery_records
        if isinstance(record.get("load_name"), str)
    }
    actual_names = [
        entry.get("flat_name")
        for entry in entries or []
        if isinstance(entry, dict) and isinstance(entry.get("flat_name"), str)
    ]
    if (
        not index_path.is_file()
        or not isinstance(entries, list)
        or index_payload.get("schemaVersion") != 1
        or index_payload.get("count") != len(entries)
        or len(actual_names) != len(entries)
        or len(actual_names) != len(set(actual_names))
        or set(actual_names) != expected_names
        or any(
            not valid_discovery_entry(entry, records_by_load_name)
            for entry in entries
        )
    ):
        add(findings, "registry.discovery-index", ["DR-08"])
    alias_names = {alias.get("alias") for alias in aliases}
    if alias_names & load_names:
        add(findings, "registry.alias-shadow", ["DR-02"])
    active_ids = {record["skill_id"] for record in skills if SKILL_FIELDS <= record.keys() and record.get("state") == "active"}
    if len(alias_names) != len(aliases) or any(alias.get("target_skill_id") not in active_ids for alias in aliases):
        add(findings, "registry.alias-target", ["DR-02"])
    if any(
        record.get("canonical_skill_id") is not None
        and (
            record["canonical_skill_id"] not in active_ids
            or record["canonical_skill_id"] == record["skill_id"]
            or known_skills[record["canonical_skill_id"]].get("canonical_skill_id")
            is not None
        )
        for record in skills
        if SKILL_FIELDS <= record.keys()
    ):
        add(findings, "registry.canonical-target", ["DR-02"])
    catalog_paths = {path.relative_to(root).as_posix() for path in discover_catalog(root)} if (root / "catalog").is_dir() else set()
    registered_paths = {record.get("catalog_path") for record in skills + quarantine if record.get("catalog_path")}
    if catalog_paths != registered_paths:
        add(findings, "registry.reconciliation", ["DR-08"], missing=sorted(catalog_paths - registered_paths), extra=sorted(registered_paths - catalog_paths))
    for exception in exceptions:
        fields = ("exception_id", "requirement_ids", "rationale", "owner", "created_at", "expires_at")
        try:
            expired = date.fromisoformat(str(exception.get("expires_at"))) < date.today()
        except ValueError:
            expired = True
        if not all(exception.get(field) for field in fields) or expired:
            add(findings, "governance.exception", ["GR-04"], exception_id=exception.get("exception_id"))
    failed = len(findings)
    return VerificationReport("pass" if not failed else "fail", 1 if not failed else 0, failed, 0, 0, tuple(findings))
