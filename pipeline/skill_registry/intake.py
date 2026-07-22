import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

import yaml

from skill_registry.hashing import tree_sha256
from skill_registry.filesystem import dump_json_atomic, load_json, write_bytes_atomic
from skill_registry.identity import stable_skill_id
from skill_registry.safety import scan_skill_bundle
from skill_registry.text import jaccard, tokenize
from skill_registry.validator import valid_safety_registry, verify_repository


SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
GITHUB_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)\.git$"
)
SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
MAX_BUNDLE_FILES = 1_000
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FILES = 10_000
MAX_SOURCE_BYTES = 250 * 1024 * 1024
SOURCE_FIELDS = ("source_id", "url", "commit", "skills_root", "license", "license_note")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class IntakeError(RuntimeError):
    pass


def slugify_load_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not value or len(value) > 128:
        raise IntakeError("invalid load name")
    return value


def next_load_name(name: str, source_id: str, used: set[str]) -> str:
    if name not in used:
        return name
    base = f"{source_id}--{name}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}--{suffix}"
        suffix += 1
    return candidate


def catalog_destination(
    root: Path, taxonomy: str, load_name: str
) -> tuple[str, Path]:
    parts = taxonomy.split("/")
    safe = re.compile(r"^[a-z0-9][a-z0-9-]*$")
    if len(parts) != 2 or not all(safe.fullmatch(part) for part in parts):
        raise IntakeError("unsafe catalog destination taxonomy")
    if not safe.fullmatch(load_name):
        raise IntakeError("unsafe catalog destination load name")
    raw_catalog = Path(root) / "catalog"
    if raw_catalog.is_symlink():
        raise IntakeError("catalog destination root is a symlink")
    catalog_root = raw_catalog.resolve()
    raw_destination = raw_catalog / parts[0] / parts[1] / load_name
    cursor = raw_catalog
    for part in [*parts, load_name]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise IntakeError("catalog destination parent is a symlink")
    destination = raw_destination.resolve()
    if not destination.is_relative_to(catalog_root):
        raise IntakeError("catalog destination escaped root")
    if destination.exists():
        raise IntakeError(
            f"catalog destination exists: {destination.relative_to(root)}"
        )
    return destination.relative_to(root).as_posix(), destination


def propose_classification(
    candidate: dict[str, object], index: list[dict[str, object]]
) -> dict[str, str]:
    candidate_tokens = tokenize(
        f"{candidate.get('name', '')} {candidate.get('description', '')}"
    )
    scores: dict[tuple[str, str], int] = {}
    for entry in index:
        taxonomy = str(entry.get("taxonomy", ""))
        category = str(entry.get("category_fine", ""))
        entry_tokens = tokenize(
            f"{entry.get('name', '')} {entry.get('description', '')} "
            f"{taxonomy} {category}"
        )
        overlap = len(candidate_tokens & entry_tokens)
        if overlap:
            key = (taxonomy, category)
            scores[key] = scores.get(key, 0) + overlap

    if scores:
        taxonomy, category = min(
            scores, key=lambda key: (-scores[key], key[0], key[1])
        )
    else:
        taxonomy = "workflows-and-management/uncategorized-and-misc"
        category = "uncategorized"
    return {
        "taxonomy": taxonomy,
        "category_fine": category,
        "classification_status": "proposed",
    }


def _normalized_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def duplicate_evidence(
    candidate: dict[str, object],
    records: list[dict[str, object]],
    index: list[dict[str, object]],
) -> list[dict[str, object]]:
    index_by_skill_id = {
        str(entry["skill_id"]): entry for entry in index if "skill_id" in entry
    }
    index_by_load_name = {
        str(entry.get("flat_name", entry.get("name", ""))): entry for entry in index
    }
    candidate_name = _normalized_name(candidate.get("name", ""))
    candidate_load_name = str(candidate.get("load_name", ""))
    candidate_tokens = tokenize(
        f"{candidate.get('name', '')} {candidate.get('description', '')}"
    )
    evidence: list[dict[str, object]] = []

    for record in records:
        skill_id = str(record["skill_id"])
        if candidate.get("content_sha256") == record.get("content_sha256"):
            evidence.append(
                {
                    "kind": "exact_hash",
                    "skill_id": skill_id,
                    "action": "canonical_candidate",
                }
            )
        if (
            candidate.get("source_id") == record.get("source_id")
            and candidate.get("source_path") == record.get("source_path")
        ):
            evidence.append(
                {
                    "kind": "same_source_path",
                    "skill_id": skill_id,
                    "action": "update_review",
                }
            )

        existing_names = {
            _normalized_name(record.get("name", "")),
            _normalized_name(record.get("load_name", "")),
        }
        if (
            (candidate_name and candidate_name in existing_names)
            or (
                candidate_load_name
                and candidate_load_name == record.get("load_name")
            )
        ):
            evidence.append(
                {
                    "kind": "name_collision",
                    "skill_id": skill_id,
                    "action": "review",
                }
            )

        metadata = index_by_skill_id.get(skill_id) or index_by_load_name.get(
            str(record.get("load_name", ""))
        )
        if metadata is not None:
            existing_tokens = tokenize(
                f"{metadata.get('name', '')} {metadata.get('description', '')}"
            )
            similarity = jaccard(candidate_tokens, existing_tokens)
            if similarity >= 0.75:
                evidence.append(
                    {
                        "kind": "functional_similarity",
                        "skill_id": skill_id,
                        "score": similarity,
                        "action": "review",
                    }
                )

    return sorted(evidence, key=lambda item: (str(item["kind"]), str(item["skill_id"])))


def validate_source_spec(spec: object) -> dict[str, str]:
    if not isinstance(spec, dict) or set(spec) != set(SOURCE_FIELDS):
        raise IntakeError("source spec fields are invalid")
    if not all(isinstance(spec[field], str) for field in SOURCE_FIELDS):
        raise IntakeError("source spec fields must be strings")

    normalized = {field: spec[field] for field in SOURCE_FIELDS}
    normalized["license"] = normalized["license"].strip()
    normalized["license_note"] = normalized["license_note"].strip()

    if SOURCE_ID.fullmatch(normalized["source_id"]) is None:
        raise IntakeError("source_id is invalid")
    if GITHUB_URL.fullmatch(normalized["url"]) is None:
        raise IntakeError("GitHub URL is invalid")
    if COMMIT.fullmatch(normalized["commit"]) is None:
        raise IntakeError("commit must be an exact 40-character SHA")

    skills_root = normalized["skills_root"]
    if (
        SAFE_RELATIVE_PATH.fullmatch(skills_root) is None
        or any(segment in {".", ".."} for segment in skills_root.split("/"))
    ):
        raise IntakeError("skills_root must be a literal relative path")
    if not normalized["license"] or not normalized["license_note"]:
        raise IntakeError("license evidence is required")
    return normalized


def preflight_source_tree(
    spec: object,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> dict[str, int]:
    normalized = validate_source_spec(spec)
    match = GITHUB_URL.fullmatch(normalized["url"])
    if match is None:
        raise IntakeError("GitHub URL is invalid")
    url = (
        f"https://api.github.com/repos/{match['owner']}/{match['repository']}"
        f"/git/trees/{normalized['commit']}?recursive=1"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "agentic-skill-registry",
        },
    )
    response = None
    try:
        response = opener(request, timeout=30)
        payload = json.load(response)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IntakeError(f"source tree preflight failed: {error}") from error
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if not isinstance(payload, dict):
        raise IntakeError("source tree preflight returned a non-object payload")
    if payload.get("truncated") is True:
        raise IntakeError("source tree response was truncated")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise IntakeError("source tree must be a list")

    file_count = 0
    byte_count = 0
    prefix = normalized["skills_root"] + "/"
    root_parts = normalized["skills_root"].split("/")
    cone_ancestor_directories = {""} | {
        "/".join(root_parts[:index]) for index in range(1, len(root_parts))
    }
    for entry in tree:
        if not isinstance(entry, dict):
            raise IntakeError("source tree entry must be an object")
        if entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            raise IntakeError("source tree blob path is invalid")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise IntakeError("source tree blob size is invalid")
        in_skills_subtree = path == normalized["skills_root"] or path.startswith(prefix)
        if not in_skills_subtree and path.rpartition("/")[0] not in cone_ancestor_directories:
            continue
        file_count += 1
        byte_count += size
        if file_count > MAX_SOURCE_FILES:
            raise IntakeError("source file limit exceeded")
        if byte_count > MAX_SOURCE_BYTES:
            raise IntakeError("source byte limit exceeded")
    return {"file_count": file_count, "byte_count": byte_count}


def checkout_pinned_source(
    spec: object,
    destination: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    normalized = validate_source_spec(spec)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise IntakeError(f"checkout destination already exists: {destination}")
    commands = [
        ["git", "init", "--", str(destination)],
        ["git", "-C", str(destination), "remote", "add", "origin", normalized["url"]],
        [
            "git",
            "-C",
            str(destination),
            "sparse-checkout",
            "set",
            "--cone",
            "--",
            normalized["skills_root"],
        ],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "credential.helper=",
            "fetch",
            "--depth",
            "1",
            "--filter=blob:none",
            "origin",
            normalized["commit"],
        ],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "credential.helper=",
            "checkout",
            "--detach",
            "FETCH_HEAD",
        ],
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
    ]
    with tempfile.TemporaryDirectory(prefix="asr-git-home-") as home:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/usr/bin/false",
            "GCM_INTERACTIVE": "never",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        try:
            results = [
                runner(
                    command,
                    timeout=60,
                    check=True,
                    text=True,
                    capture_output=True,
                    env=env,
                )
                for command in commands
            ]
        except (OSError, subprocess.SubprocessError) as error:
            raise IntakeError(f"pinned source checkout failed: {error}") from error
    if results[-1].stdout.strip() != normalized["commit"]:
        raise IntakeError("checked out commit does not match requested commit")


def discover_source_bundles(skills_root: Path) -> list[Path]:
    root = Path(skills_root)
    if root.is_symlink():
        raise IntakeError(f"symlink skills_root rejected: {root}")
    if not root.is_dir():
        raise IntakeError(f"skills_root is not a directory: {root}")
    bundles: list[Path] = []

    def visit(directory: Path) -> None:
        marker = directory / "SKILL.md"
        if marker.is_file():
            bundles.append(directory)
            return
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                raise IntakeError(f"symlink rejected: {child.relative_to(root)}")
            if child.is_dir():
                visit(child)

    visit(root)
    return sorted(bundles, key=lambda item: item.relative_to(root).as_posix())


def parse_skill_frontmatter(path: Path) -> dict[str, object]:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise IntakeError(f"SKILL.md frontmatter could not be read: {error}") from error
    match = re.match(r"^---\s*\n(.*?)\n---(?:\s*\n|$)", content, re.DOTALL)
    if match is None:
        raise IntakeError("SKILL.md frontmatter is missing")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise IntakeError(f"SKILL.md frontmatter is malformed: {error}") from error
    if not isinstance(metadata, dict):
        raise IntakeError("SKILL.md frontmatter must be an object")
    for field in ("name", "description"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise IntakeError(f"SKILL.md frontmatter {field} must be a non-empty string")
    return metadata


def inspect_bundle(bundle: Path) -> dict[str, object]:
    bundle = Path(bundle)
    if bundle.is_symlink():
        raise IntakeError(f"symlink bundle root rejected: {bundle}")
    marker = bundle / "SKILL.md"
    if not marker.is_file():
        raise IntakeError("bundle root SKILL.md is missing")
    files = []
    total = 0
    for path in sorted(bundle.rglob("*"), key=lambda item: item.relative_to(bundle).as_posix()):
        relative = path.relative_to(bundle)
        if path.is_symlink():
            raise IntakeError(f"symlink rejected: {relative}")
        if path.is_file():
            stat = path.stat()
            if stat.st_nlink > 1:
                raise IntakeError(f"hardlink rejected: {relative}")
            files.append(path)
            total += stat.st_size
    if len(files) > MAX_BUNDLE_FILES:
        raise IntakeError("bundle file limit exceeded")
    if total > MAX_BUNDLE_BYTES:
        raise IntakeError("bundle byte limit exceeded")
    metadata = parse_skill_frontmatter(marker)
    return {
        "name": metadata["name"].strip(),
        "description": metadata["description"].strip(),
        "file_count": len(files),
        "byte_count": total,
        "content_sha256": tree_sha256(bundle),
    }


def _object_list(path: Path, key: str) -> list[dict[str, object]]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise IntakeError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise IntakeError(f"expected object: {path}")
    values = payload.get(key)
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise IntakeError(f"invalid {key}: {path}")
    return values


def prepare_source(root: Path, spec: object, staging: Path) -> dict[str, object]:
    root = Path(root)
    staging = Path(staging)
    if staging.exists() or staging.is_symlink():
        raise IntakeError(f"staging already exists: {staging}")
    if staging.resolve().is_relative_to(root.resolve()):
        raise IntakeError(f"staging must not be inside repository: {staging}")
    normalized = validate_source_spec(spec)
    sources = _object_list(root / "registry" / "sources.lock.json", "sources")
    if any(source.get("source_id") == normalized["source_id"] for source in sources):
        raise IntakeError(f"source_id already exists: {normalized['source_id']}")

    records = _object_list(root / "registry" / "skills.json", "skills")
    records += _object_list(root / "registry" / "quarantine.json", "records")
    index = _object_list(root / "librarian-index.json", "entries")

    preflight_source_tree(normalized)
    candidates: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="asr-source-") as temporary:
        checkout = Path(temporary) / "source"
        checkout_pinned_source(normalized, checkout)
        for bundle in discover_source_bundles(checkout / normalized["skills_root"]):
            source_path = bundle.relative_to(checkout).as_posix()
            inspected = inspect_bundle(bundle)
            classification = propose_classification(inspected, index)
            evidence = duplicate_evidence(
                {
                    **inspected,
                    "source_id": normalized["source_id"],
                    "source_path": source_path,
                    "load_name": inspected["name"],
                },
                records,
                index,
            )
            candidates.append(
                {
                    "source_path": source_path,
                    "name": inspected["name"],
                    "description": inspected["description"],
                    "content_sha256": inspected["content_sha256"],
                    "safety_profile": scan_skill_bundle(
                        bundle, str(inspected["content_sha256"])
                    ),
                    "file_count": inspected["file_count"],
                    "byte_count": inspected["byte_count"],
                    "proposed_taxonomy": classification["taxonomy"],
                    "proposed_category_fine": classification["category_fine"],
                    "duplicate_evidence": evidence,
                }
            )

    candidates.sort(key=lambda candidate: str(candidate["source_path"]))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "source": normalized,
        "candidates": candidates,
    }
    staging.mkdir()
    try:
        manifest_path = staging / "manifest.json"
        dump_json_atomic(manifest_path, manifest)
        manifest_bytes = manifest_path.read_bytes()
        review = {
            "schema_version": 1,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "decisions": [
                {
                    "source_path": candidate["source_path"],
                    "decision": "pending",
                    "taxonomy": candidate["proposed_taxonomy"],
                    "category_fine": candidate["proposed_category_fine"],
                    "canonical_skill_id": None,
                    "reason": "",
                }
                for candidate in candidates
            ],
        }
        dump_json_atomic(staging / "review.json", review)
    except Exception:
        shutil.rmtree(staging)
        raise
    return manifest


def validate_review(
    manifest_bytes: bytes,
    review: object,
    known_skill_ids: set[str],
) -> None:
    try:
        manifest = json.loads(manifest_bytes)
    except (TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise IntakeError(f"manifest is invalid: {error}") from error
    if not isinstance(manifest, dict):
        raise IntakeError("manifest must be an object")
    source = manifest.get("source")
    candidates = manifest.get("candidates")
    if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
        raise IntakeError("manifest source is invalid")
    if not isinstance(candidates, list) or not all(
        isinstance(candidate, dict) and isinstance(candidate.get("source_path"), str)
        for candidate in candidates
    ):
        raise IntakeError("manifest candidates are invalid")

    if not isinstance(review, dict) or set(review) != {
        "schema_version",
        "manifest_sha256",
        "decisions",
    }:
        raise IntakeError("review fields are invalid")
    if review["schema_version"] != 1:
        raise IntakeError("review schema_version is invalid")
    expected_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if review["manifest_sha256"] != expected_digest:
        raise IntakeError("manifest digest mismatch")
    decisions = review["decisions"]
    if not isinstance(decisions, list) or not all(
        isinstance(decision, dict) for decision in decisions
    ):
        raise IntakeError("review decisions are invalid")

    manifest_paths = [str(candidate["source_path"]) for candidate in candidates]
    review_paths = [decision.get("source_path") for decision in decisions]
    if len(set(manifest_paths)) != len(manifest_paths):
        raise IntakeError("manifest source paths are duplicated")
    if (
        not all(isinstance(path, str) for path in review_paths)
        or len(set(review_paths)) != len(review_paths)
        or set(review_paths) != set(manifest_paths)
    ):
        raise IntakeError("review must contain exactly one decision per candidate")

    required_fields = {
        "source_path",
        "decision",
        "taxonomy",
        "category_fine",
        "canonical_skill_id",
        "reason",
    }
    for decision in decisions:
        if set(decision) != required_fields:
            raise IntakeError("review decision fields are invalid")
        action = decision["decision"]
        if action not in {"import", "canonical", "quarantine", "reject"}:
            raise IntakeError("review decision is invalid")
        reason = decision["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise IntakeError("review reason is required")
        taxonomy = decision["taxonomy"]
        taxonomy_parts = taxonomy.split("/") if isinstance(taxonomy, str) else []
        if len(taxonomy_parts) != 2 or not all(
            SLUG.fullmatch(part) for part in taxonomy_parts
        ):
            raise IntakeError("review taxonomy is invalid")
        category = decision["category_fine"]
        if not isinstance(category, str) or SLUG.fullmatch(category) is None:
            raise IntakeError("review category is invalid")

        canonical_skill_id = decision["canonical_skill_id"]
        self_skill_id = stable_skill_id(
            source["source_id"], str(decision["source_path"])
        )
        if action == "canonical":
            if (
                not isinstance(canonical_skill_id, str)
                or canonical_skill_id not in known_skill_ids
                or canonical_skill_id == self_skill_id
            ):
                raise IntakeError("canonical target is invalid")
        elif canonical_skill_id is not None:
            raise IntakeError("non-canonical decision cannot have a canonical target")


def source_review_artifact(
    source: dict[str, str],
    manifest_bytes: bytes,
    candidates: list[dict[str, object]],
    decisions: list[dict[str, object]],
) -> dict[str, object]:
    candidate_by_path = {
        str(item["source_path"]): item for item in candidates
    }
    records = []
    for decision in sorted(decisions, key=lambda item: str(item["source_path"])):
        candidate = candidate_by_path[str(decision["source_path"])]
        records.append(
            {
                "source_path": decision["source_path"],
                "content_sha256": candidate["content_sha256"],
                "decision": decision["decision"],
                "taxonomy": decision["taxonomy"],
                "category_fine": decision["category_fine"],
                "canonical_skill_id": decision["canonical_skill_id"],
                "reason": decision["reason"],
            }
        )
    return {
        "schema_version": 1,
        "source_id": source["source_id"],
        "source_commit": source["commit"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "candidate_count": len(records),
        "decisions": records,
    }


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        value = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise IntakeError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise IntakeError(f"expected object: {path}")
    return value


def _require_clean_worktree(root: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise IntakeError(f"cannot verify clean worktree: {error}") from error
    if result.stdout:
        raise IntakeError("commit requires a clean worktree")


def _restore_bytes_atomic(path: Path, content: bytes) -> None:
    write_bytes_atomic(path, content)


def _validate_commit_objects(
    sources_payload: dict[str, object],
    skills_payload: dict[str, object],
    quarantine_payload: dict[str, object],
) -> None:
    if sources_payload.get("schema_version") != 1:
        raise IntakeError("source lock schema_version is invalid")
    if skills_payload.get("schema_version") != 1:
        raise IntakeError("skills schema_version is invalid")
    if quarantine_payload.get("schema_version") != 1:
        raise IntakeError("quarantine schema_version is invalid")
    sources = sources_payload.get("sources")
    skills = skills_payload.get("skills")
    quarantine = quarantine_payload.get("records")
    if not isinstance(sources, list) or not all(isinstance(item, dict) for item in sources):
        raise IntakeError("source lock records are invalid")
    if not isinstance(skills, list) or not all(isinstance(item, dict) for item in skills):
        raise IntakeError("skill records are invalid")
    if not isinstance(quarantine, list) or not all(
        isinstance(item, dict) for item in quarantine
    ):
        raise IntakeError("quarantine records are invalid")

    source_ids = [source.get("source_id") for source in sources]
    if not all(isinstance(source_id, str) for source_id in source_ids) or len(
        source_ids
    ) != len(set(source_ids)):
        raise IntakeError("source lock must contain unique source IDs")
    source_by_id = {str(source["source_id"]): source for source in sources}
    records = [*skills, *quarantine]
    skill_ids = [record.get("skill_id") for record in records]
    load_names = [
        record["load_name"]
        for record in records
        if isinstance(record.get("load_name"), str)
    ]
    catalog_paths = [
        record["catalog_path"]
        for record in records
        if isinstance(record.get("catalog_path"), str)
    ]
    if len(skill_ids) != len(set(skill_ids)):
        raise IntakeError("skill IDs must be unique")
    if len(load_names) != len(set(load_names)):
        raise IntakeError("load names must be unique")
    if len(catalog_paths) != len(set(catalog_paths)):
        raise IntakeError("catalog destinations must be unique")
    for record in records:
        source = source_by_id.get(str(record.get("source_id")))
        if source is None or record.get("source_commit") != source.get("commit"):
            raise IntakeError("record does not join exactly one locked source")

    active_by_id = {
        str(record.get("skill_id")): record
        for record in skills
        if record.get("state") == "active"
    }
    for record in skills:
        target_id = record.get("canonical_skill_id")
        if target_id is None:
            continue
        target = active_by_id.get(str(target_id))
        if target is None or target.get("canonical_skill_id") is not None:
            raise IntakeError("canonical target must be active and non-canonical")


def _create_parent_directories(
    destination: Path, catalog_root: Path, created: list[Path]
) -> None:
    missing: list[Path] = []
    cursor = destination.parent
    while cursor != catalog_root and not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def commit_source(
    root: Path, manifest_path: Path, review_path: Path
) -> dict[str, object]:
    root = Path(root)
    try:
        manifest_bytes = Path(manifest_path).read_bytes()
        review_bytes = Path(review_path).read_bytes()
    except OSError as error:
        raise IntakeError(f"cannot read intake files: {error}") from error
    try:
        review = json.loads(review_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IntakeError(f"review is invalid: {error}") from error

    current_skills = _object_list(root / "registry/skills.json", "skills")
    current_quarantine = _object_list(
        root / "registry/quarantine.json", "records"
    )
    known_skill_ids = {
        str(record["skill_id"])
        for record in [*current_skills, *current_quarantine]
        if isinstance(record.get("skill_id"), str)
    }
    validate_review(manifest_bytes, review, known_skill_ids)
    _require_clean_worktree(root)

    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IntakeError(f"manifest is invalid: {error}") from error
    if not isinstance(manifest, dict):
        raise IntakeError("manifest must be an object")
    source = validate_source_spec(manifest.get("source"))
    candidates = manifest.get("candidates")
    decisions = review.get("decisions")
    if not isinstance(candidates, list) or not isinstance(decisions, list):
        raise IntakeError("manifest or review candidates are invalid")
    candidate_by_path = {
        str(candidate["source_path"]): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("source_path"), str)
    }
    decision_by_path = {
        str(decision["source_path"]): decision
        for decision in decisions
        if isinstance(decision, dict) and isinstance(decision.get("source_path"), str)
    }

    json_paths = [
        root / "registry/sources.lock.json",
        root / "registry/skills.json",
        root / "registry/quarantine.json",
        root / "librarian-index.json",
        root / "registry/safety-signals.json",
    ]
    original_bytes: dict[Path, bytes] = {}
    json_mutation_started = False
    sources_payload = _load_json_object(json_paths[0])
    skills_payload = _load_json_object(json_paths[1])
    quarantine_payload = _load_json_object(json_paths[2])
    index_payload = _load_json_object(json_paths[3])
    safety_payload = _load_json_object(json_paths[4])
    sources = sources_payload.get("sources")
    skills = skills_payload.get("skills")
    quarantine = quarantine_payload.get("records")
    entries = index_payload.get("entries")
    if (
        not isinstance(sources, list)
        or not all(isinstance(item, dict) for item in sources)
        or not isinstance(skills, list)
        or not all(isinstance(item, dict) for item in skills)
    ):
        raise IntakeError("registry records are invalid")
    if (
        not isinstance(quarantine, list)
        or not all(isinstance(item, dict) for item in quarantine)
        or not isinstance(entries, list)
        or not all(isinstance(item, dict) for item in entries)
    ):
        raise IntakeError("registry records are invalid")
    if any(
        isinstance(item, dict) and item.get("source_id") == source["source_id"]
        for item in sources
    ):
        raise IntakeError(f"source_id already exists: {source['source_id']}")
    schema = _load_json_object(root / "registry/schema-version.json")
    if schema.get("schema_version") != 1:
        raise IntakeError("registry schema_version must remain 1")
    artifact_relative = (
        "registry/source-reviews/"
        f"{source['source_id']}-{source['commit']}.json"
    )
    artifact_path = root / artifact_relative
    if artifact_path.exists() or artifact_path.is_symlink():
        raise IntakeError(f"source review artifact already exists: {artifact_relative}")
    artifact = source_review_artifact(
        source, manifest_bytes, candidates, decisions
    )

    created_destinations: list[Path] = []
    created_parents: list[Path] = []
    temporary_copies: list[Path] = []
    artifact_created = False
    try:
        preflight_source_tree(source)
        with tempfile.TemporaryDirectory(prefix="asr-commit-source-") as temporary:
            checkout = Path(temporary) / "source"
            checkout_pinned_source(source, checkout)
            discovered = {
                bundle.relative_to(checkout).as_posix(): bundle
                for bundle in discover_source_bundles(checkout / source["skills_root"])
            }
            discovered_paths = set(discovered)
            manifest_paths = set(candidate_by_path)
            review_paths = set(decision_by_path)
            if discovered_paths != manifest_paths or manifest_paths != review_paths:
                raise IntakeError(
                    "pinned candidate set differs from reviewed manifest: "
                    f"discovered={len(discovered_paths)} "
                    f"manifest={len(manifest_paths)} review={len(review_paths)}"
                )
            inspected_by_path: dict[str, tuple[Path, dict[str, object]]] = {}
            for source_path in sorted(manifest_paths):
                bundle = discovered[source_path]
                inspected = inspect_bundle(bundle)
                expected = candidate_by_path[source_path].get("content_sha256")
                if inspected["content_sha256"] != expected:
                    raise IntakeError(
                        f"reviewed candidate changed since preparation: {source_path}"
                    )
                if decision_by_path[source_path]["decision"] != "reject":
                    inspected_by_path[source_path] = (bundle, inspected)

            used_names = {
                str(record["load_name"])
                for record in [*skills, *quarantine]
                if isinstance(record, dict) and isinstance(record.get("load_name"), str)
            }
            destinations: dict[str, tuple[str, Path, str]] = {}
            for source_path in sorted(inspected_by_path):
                decision = decision_by_path[source_path]
                inspected = inspected_by_path[source_path][1]
                load_name = next_load_name(
                    slugify_load_name(str(inspected["name"])),
                    source["source_id"],
                    used_names,
                )
                used_names.add(load_name)
                catalog_path, destination = catalog_destination(
                    root, str(decision["taxonomy"]), load_name
                )
                destinations[source_path] = (catalog_path, destination, load_name)
            destination_paths = [value[1] for value in destinations.values()]
            if len(destination_paths) != len(set(destination_paths)):
                raise IntakeError("catalog destinations must be unique")

            source_record = {
                "source_id": source["source_id"],
                "url": source["url"],
                "commit": source["commit"],
                "layout": "skills-subdir",
                "skills_root": source["skills_root"],
                "metadata_index": None,
                "license_note": source["license_note"],
                "status": "active",
                "refreshable": True,
                "timeout_seconds": 15,
                "review": {
                    "status": "reviewed",
                    "artifact": artifact_relative,
                    "manifest_sha256": artifact["manifest_sha256"],
                },
            }
            new_sources = {**sources_payload, "sources": [*sources, source_record]}
            new_skills = list(skills)
            new_quarantine = list(quarantine)
            new_entries = list(entries)
            for source_path in sorted(inspected_by_path):
                decision = decision_by_path[source_path]
                inspected = inspected_by_path[source_path][1]
                catalog_path, _, load_name = destinations[source_path]
                record = {
                    "skill_id": stable_skill_id(source["source_id"], source_path),
                    "name": inspected["name"],
                    "load_name": load_name,
                    "catalog_path": catalog_path,
                    "source_id": source["source_id"],
                    "source_commit": source["commit"],
                    "source_path": source_path,
                    "content_sha256": inspected["content_sha256"],
                    "license": source["license"],
                    "risk": "unknown",
                    "risk_reasons": ["initial-review-required"],
                    "state": (
                        "quarantined"
                        if decision["decision"] == "quarantine"
                        else "active"
                    ),
                    "canonical_skill_id": decision["canonical_skill_id"],
                    "first_seen_version": "0.2.0",
                }
                if decision["decision"] == "quarantine":
                    new_quarantine.append(
                        {
                            **record,
                            "rule_ids": ["initial-review-required"],
                            "disposition": "quarantined",
                        }
                    )
                else:
                    new_skills.append(record)
                new_entries.append(
                    {
                        "skill_id": record["skill_id"],
                        "name": inspected["name"],
                        "flat_name": load_name,
                        "taxonomy": decision["taxonomy"],
                        "category_fine": decision["category_fine"],
                        "description": inspected["description"],
                        "risk": "unknown",
                        "license": source["license"],
                        "canonical": decision["canonical_skill_id"],
                    }
                )
            new_skills_payload = {**skills_payload, "skills": new_skills}
            new_quarantine_payload = {
                **quarantine_payload,
                "records": new_quarantine,
            }
            new_index_payload = {**index_payload, "entries": new_entries}
            new_index_payload["count"] = len(new_entries)
            profiles = safety_payload.get("profiles")
            if not isinstance(profiles, list) or not all(
                isinstance(profile, dict) for profile in profiles
            ):
                raise IntakeError("safety profile records are invalid")
            profiles_by_id = {
                str(profile.get("skill_id")): profile for profile in profiles
            }
            for source_path in sorted(inspected_by_path):
                decision = decision_by_path[source_path]
                if decision["decision"] == "quarantine":
                    continue
                record = next(
                    item
                    for item in new_skills
                    if item["skill_id"]
                    == stable_skill_id(source["source_id"], source_path)
                )
                bundle, inspected = inspected_by_path[source_path]
                profiles_by_id[record["skill_id"]] = {
                    "skill_id": record["skill_id"],
                    **scan_skill_bundle(bundle, str(inspected["content_sha256"])),
                }
            new_safety_payload = {
                "schema_version": 1,
                "profiles": [
                    profiles_by_id[str(record["skill_id"])]
                    for record in sorted(
                        (
                            record
                            for record in new_skills
                            if record.get("state") == "active"
                        ),
                        key=lambda record: str(record["skill_id"]),
                    )
                    if str(record["skill_id"]) in profiles_by_id
                ],
            }
            _validate_commit_objects(
                new_sources, new_skills_payload, new_quarantine_payload
            )
            if not valid_safety_registry(
                new_safety_payload, new_skills
            ):
                raise IntakeError("safety profile records are invalid")

            catalog_root = (root / "catalog").resolve()
            for source_path in sorted(inspected_by_path):
                bundle = inspected_by_path[source_path][0]
                destination = destinations[source_path][1]
                _create_parent_directories(
                    destination, catalog_root, created_parents
                )
                temporary_copy = Path(
                    tempfile.mkdtemp(
                        dir=destination.parent, prefix=f".{destination.name}."
                    )
                )
                temporary_copies.append(temporary_copy)
                if not temporary_copy.resolve().is_relative_to(catalog_root):
                    raise IntakeError("catalog temporary destination escaped root")
                shutil.copytree(bundle, temporary_copy, dirs_exist_ok=True)
                temporary_copy.replace(destination)
                temporary_copies.remove(temporary_copy)
                created_destinations.append(destination)

            original_bytes = {path: path.read_bytes() for path in json_paths}
            for path, payload in zip(
                json_paths,
                [
                    new_sources,
                    new_skills_payload,
                    new_quarantine_payload,
                    new_index_payload,
                    new_safety_payload,
                ],
            ):
                dump_json_atomic(path, payload)
                json_mutation_started = True
            dump_json_atomic(artifact_path, artifact)
            artifact_created = True
            report = verify_repository(root)
            if report.result != "pass":
                check_ids = sorted(
                    str(finding.get("check_id")) for finding in report.findings
                )
                raise IntakeError(
                    f"post-commit strict verification failed: {', '.join(check_ids)}"
                )
    except Exception:
        if json_mutation_started:
            for path, content in original_bytes.items():
                _restore_bytes_atomic(path, content)
        if artifact_created:
            artifact_path.unlink(missing_ok=True)
        for temporary_copy in temporary_copies:
            shutil.rmtree(temporary_copy, ignore_errors=True)
        for destination in reversed(created_destinations):
            shutil.rmtree(destination, ignore_errors=True)
        for directory in reversed(created_parents):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise

    decision_names = [str(decision["decision"]) for decision in decisions]
    return {
        "canonical": decision_names.count("canonical"),
        "imported": decision_names.count("import"),
        "quarantined": decision_names.count("quarantine"),
        "rejected": decision_names.count("reject"),
        "result": "pass",
        "strict_verifier": "pass",
    }
