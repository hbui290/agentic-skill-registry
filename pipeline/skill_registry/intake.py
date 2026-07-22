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
    scores: list[tuple[int, float, str, str]] = []
    for entry in index:
        taxonomy = str(entry.get("taxonomy", ""))
        category = str(entry.get("category_fine", ""))
        entry_tokens = tokenize(
            f"{entry.get('name', '')} {entry.get('description', '')} "
            f"{taxonomy} {category}"
        )
        overlap = len(candidate_tokens & entry_tokens)
        if overlap:
            scores.append(
                (
                    overlap,
                    jaccard(candidate_tokens, entry_tokens),
                    taxonomy,
                    category,
                )
            )

    if scores:
        _, _, taxonomy, category = min(
            scores,
            key=lambda item: (-item[0], -item[1], item[2], item[3]),
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


def discovery_metadata_for_record(
    record: dict[str, object], index: list[dict[str, object]]
) -> dict[str, object] | None:
    skill_id = record.get("skill_id")
    load_name = record.get("load_name")
    return next(
        (
            entry
            for entry in index
            if entry.get("skill_id") == skill_id
            or entry.get("flat_name") == load_name
        ),
        None,
    )


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


def indexed_source_bundles(
    checkout: Path, metadata_index: str, skills_root: str
) -> tuple[dict[str, Path], dict[str, dict[str, object]]]:
    checkout = Path(checkout)
    index_path = checkout / metadata_index
    try:
        if index_path.is_file() and not index_path.is_symlink():
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        else:
            with tempfile.TemporaryDirectory(prefix="asr-git-home-") as home:
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(checkout),
                        "-c",
                        "credential.helper=",
                        "show",
                        f"HEAD:{metadata_index}",
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                    timeout=60,
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": home,
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_ASKPASS": "/usr/bin/false",
                        "GCM_INTERACTIVE": "never",
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_ATTR_NOSYSTEM": "1",
                        "GIT_LFS_SKIP_SMUDGE": "1",
                    },
                )
            payload = json.loads(result.stdout)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        raise IntakeError(f"metadata index could not be read: {error}") from error
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise IntakeError("metadata index must be a list of objects")

    bundles: dict[str, Path] = {}
    metadata: dict[str, dict[str, object]] = {}
    prefix = skills_root + "/"
    checkout_root = checkout.resolve()
    for item in payload:
        source_path = item.get("path")
        if (
            not isinstance(source_path, str)
            or SAFE_RELATIVE_PATH.fullmatch(source_path) is None
            or not source_path.startswith(prefix)
            or any(part in {".", ".."} for part in source_path.split("/"))
            or source_path in bundles
        ):
            raise IntakeError("metadata index source path is invalid")
        bundle = checkout / source_path
        if bundle.is_symlink() or not bundle.is_dir():
            raise IntakeError(f"indexed bundle root is invalid: {source_path}")
        if not bundle.resolve().is_relative_to(checkout_root):
            raise IntakeError(f"indexed bundle escaped checkout: {source_path}")
        if not (bundle / "SKILL.md").is_file():
            raise IntakeError(f"indexed bundle SKILL.md is missing: {source_path}")
        bundles[source_path] = bundle
        metadata[source_path] = item
    return bundles, metadata


def reconcile_indexed_source_paths(
    records: list[dict[str, object]],
    metadata: dict[str, dict[str, object]],
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    path_by_id: dict[str, str] = {}
    for source_path, item in metadata.items():
        index_id = item.get("id")
        if (
            not isinstance(index_id, str)
            or not index_id
            or index_id in path_by_id
        ):
            raise IntakeError("metadata index IDs must be unique strings")
        path_by_id[index_id] = source_path
    mapped: dict[str, dict[str, object]] = {}
    corrections: list[dict[str, str]] = []
    for record in records:
        load_name = record.get("load_name")
        source_path = record.get("source_path")
        skill_id = record.get("skill_id")
        if (
            not isinstance(source_path, str)
            or not isinstance(skill_id, str)
        ):
            raise IntakeError("registry record does not match metadata index")
        if source_path in metadata:
            indexed_path = source_path
        elif isinstance(load_name, str) and load_name in path_by_id:
            indexed_path = path_by_id[load_name]
        else:
            raise IntakeError("registry record does not match metadata index")
        if indexed_path in mapped:
            raise IntakeError("metadata index mapping is not unique")
        mapped[indexed_path] = record
        if indexed_path != source_path:
            corrections.append(
                {"from": source_path, "skill_id": skill_id, "to": indexed_path}
            )
    if set(mapped) != set(metadata):
        raise IntakeError("existing registry does not cover metadata index")
    return mapped, sorted(corrections, key=lambda item: item["from"])


def source_object_id(checkout: Path, source_path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", f"HEAD:{source_path}"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return tree_sha256(Path(checkout) / source_path)
    object_id = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", object_id):
        raise IntakeError(f"source object identity is invalid: {source_path}")
    return object_id


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


def inspect_bundle(
    bundle: Path,
    max_files: int | None = None,
    max_bytes: int | None = None,
) -> dict[str, object]:
    if max_files is None:
        max_files = MAX_BUNDLE_FILES
    if max_bytes is None:
        max_bytes = MAX_BUNDLE_BYTES
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
    if len(files) > max_files:
        raise IntakeError("bundle file limit exceeded")
    if total > max_bytes:
        raise IntakeError("bundle byte limit exceeded")
    metadata = parse_skill_frontmatter(marker)
    return {
        "name": metadata["name"].strip(),
        "description": metadata["description"].strip(),
        "file_count": len(files),
        "byte_count": total,
        "content_sha256": tree_sha256(bundle),
    }


def _bundle_size(bundle: Path) -> tuple[int, int]:
    count = 0
    total = 0
    for path in Path(bundle).rglob("*"):
        if path.is_symlink():
            raise IntakeError(f"symlink rejected: {path.relative_to(bundle)}")
        if path.is_file():
            stat = path.stat()
            if stat.st_nlink > 1:
                raise IntakeError(f"hardlink rejected: {path.relative_to(bundle)}")
            count += 1
            total += stat.st_size
    return count, total


def inspect_update_bundle(bundle: Path, base_bundle: Path) -> dict[str, object]:
    base_count, base_bytes = _bundle_size(base_bundle)
    target_count, target_bytes = _bundle_size(bundle)
    max_files = MAX_BUNDLE_FILES if base_count <= MAX_BUNDLE_FILES else base_count
    max_bytes = MAX_BUNDLE_BYTES if base_bytes <= MAX_BUNDLE_BYTES else base_bytes
    if target_count > max_files:
        raise IntakeError("bundle file limit exceeded")
    if target_bytes > max_bytes:
        raise IntakeError("bundle byte limit exceeded")
    return inspect_bundle(bundle, max_files=max_files, max_bytes=max_bytes)


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


def prepare_source_update(
    root: Path, spec: object, staging: Path
) -> dict[str, object]:
    root = Path(root)
    staging = Path(staging)
    if staging.exists() or staging.is_symlink():
        raise IntakeError(f"staging already exists: {staging}")
    if staging.resolve().is_relative_to(root.resolve()):
        raise IntakeError(f"staging must not be inside repository: {staging}")
    normalized = validate_source_spec(spec)
    sources = _object_list(root / "registry/sources.lock.json", "sources")
    existing_source = next(
        (
            source
            for source in sources
            if source.get("source_id") == normalized["source_id"]
        ),
        None,
    )
    if existing_source is None:
        raise IntakeError(f"source_id does not exist: {normalized['source_id']}")
    if (
        existing_source.get("url") != normalized["url"]
        or existing_source.get("skills_root") != normalized["skills_root"]
    ):
        raise IntakeError("source update identity does not match source lock")
    base_commit = existing_source.get("commit")
    if not isinstance(base_commit, str) or COMMIT.fullmatch(base_commit) is None:
        raise IntakeError("existing source commit is invalid")
    if base_commit == normalized["commit"]:
        raise IntakeError("source update commit is already pinned")

    records = _object_list(root / "registry/skills.json", "skills")
    records += _object_list(root / "registry/quarantine.json", "records")
    source_records = [
        record
        for record in records
        if record.get("source_id") == normalized["source_id"]
    ]
    index = _object_list(root / "librarian-index.json", "entries")
    profiles = _object_list(
        root / "registry/safety-signals.json", "profiles"
    )
    profile_by_id = {
        str(profile.get("skill_id")): profile
        for profile in profiles
        if isinstance(profile.get("skill_id"), str)
    }

    preflight_source_tree(normalized)
    with tempfile.TemporaryDirectory(prefix="asr-source-update-") as temporary:
        temporary_root = Path(temporary)
        base_checkout = temporary_root / "base"
        target_checkout = temporary_root / "target"
        base_spec = {**normalized, "commit": base_commit}
        checkout_pinned_source(base_spec, base_checkout)
        checkout_pinned_source(normalized, target_checkout)
        metadata_index = existing_source.get("metadata_index")
        if isinstance(metadata_index, str) and metadata_index:
            base_bundles, base_source_metadata = indexed_source_bundles(
                base_checkout, metadata_index, normalized["skills_root"]
            )
            target_bundles, target_source_metadata = indexed_source_bundles(
                target_checkout, metadata_index, normalized["skills_root"]
            )
        else:
            base_bundles = {
                bundle.relative_to(base_checkout).as_posix(): bundle
                for bundle in discover_source_bundles(
                    base_checkout / normalized["skills_root"]
                )
            }
            target_bundles = {
                bundle.relative_to(target_checkout).as_posix(): bundle
                for bundle in discover_source_bundles(
                    target_checkout / normalized["skills_root"]
                )
            }
            base_source_metadata = {path: {} for path in base_bundles}
            target_source_metadata = {path: {} for path in target_bundles}
        base_object_ids = {
            path: source_object_id(base_checkout, path) for path in base_bundles
        }
        target_object_ids = {
            path: source_object_id(target_checkout, path)
            for path in target_bundles
        }

        for record in source_records:
            digest = str(record.get("content_sha256", ""))
            catalog_path = record.get("catalog_path")
            if not isinstance(catalog_path, str) or tree_sha256(
                root / catalog_path
            ) != digest:
                raise IntakeError("existing catalog content does not match registry")
        if isinstance(metadata_index, str) and metadata_index:
            record_by_base_path, corrections = reconcile_indexed_source_paths(
                source_records, base_source_metadata
            )
        else:
            record_by_base_path = {}
            corrections = []
            for record in source_records:
                source_path = str(record.get("source_path", ""))
                matches = [
                    path
                    for path in base_bundles
                    if (
                        path == source_path
                        or path.rsplit("/", 1)[-1]
                        == source_path.rsplit("/", 1)[-1]
                    )
                ]
                if len(matches) != 1:
                    raise IntakeError(
                        "existing source path cannot be reconciled: "
                        f"{source_path}"
                    )
                matched_path = matches[0]
                if matched_path in record_by_base_path:
                    raise IntakeError("existing source paths are not unique")
                record_by_base_path[matched_path] = record
                if matched_path != source_path:
                    corrections.append(
                        {
                            "from": source_path,
                            "skill_id": str(record["skill_id"]),
                            "to": matched_path,
                        }
                    )
        if set(record_by_base_path) != set(base_bundles):
            raise IntakeError("existing registry does not cover pinned source snapshot")
        removed = sorted(set(base_bundles) - set(target_bundles))
        if removed:
            raise IntakeError("source removals are not supported")

        candidates: list[dict[str, object]] = []
        for source_path in sorted(target_bundles):
            existing = record_by_base_path.get(source_path)
            indexed_license = target_source_metadata[source_path].get("license")
            target_license = (
                indexed_license.strip()
                if isinstance(indexed_license, str) and indexed_license.strip()
                else None
            )
            existing_license = existing.get("license") if existing else None
            license_changed = (
                existing is not None
                and target_license is not None
                and target_license != existing_license
            )
            unchanged = (
                existing is not None
                and base_object_ids[source_path] == target_object_ids[source_path]
            )
            corrected = (
                existing is not None
                and existing.get("source_path") != source_path
            )
            if unchanged and not corrected and not license_changed:
                continue
            metadata = (
                discovery_metadata_for_record(existing, index)
                if existing
                else None
            )
            if unchanged and existing is not None:
                if metadata is None:
                    raise IntakeError("existing discovery metadata is missing")
                catalog_path = str(existing["catalog_path"])
                file_count, byte_count = _bundle_size(root / catalog_path)
                inspected = {
                    "name": existing["name"],
                    "description": metadata["description"],
                    "content_sha256": existing["content_sha256"],
                    "file_count": file_count,
                    "byte_count": byte_count,
                }
                profile = profile_by_id.get(str(existing["skill_id"]))
                if profile is None:
                    if existing.get("state") != "quarantined":
                        raise IntakeError("existing safety profile is missing")
                    safety_profile = scan_skill_bundle(
                        target_bundles[source_path],
                        str(inspected["content_sha256"]),
                    )
                else:
                    safety_profile = {
                        key: value
                        for key, value in profile.items()
                        if key != "skill_id"
                    }
            else:
                inspected = (
                    inspect_update_bundle(
                        target_bundles[source_path], base_bundles[source_path]
                    )
                    if existing is not None
                    else inspect_bundle(target_bundles[source_path])
                )
                safety_profile = scan_skill_bundle(
                    target_bundles[source_path],
                    str(inspected["content_sha256"]),
                )
            classification = (
                {
                    "taxonomy": metadata["taxonomy"],
                    "category_fine": metadata["category_fine"],
                }
                if metadata is not None
                else propose_classification(inspected, index)
            )
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
                    "change": (
                        "path-corrected"
                        if unchanged and not license_changed
                        else "modified" if existing is not None else "added"
                    ),
                    "name": inspected["name"],
                    "description": inspected["description"],
                    "content_sha256": inspected["content_sha256"],
                    "safety_profile": safety_profile,
                    "file_count": inspected["file_count"],
                    "byte_count": inspected["byte_count"],
                    "proposed_taxonomy": classification["taxonomy"],
                    "proposed_category_fine": classification["category_fine"],
                    "duplicate_evidence": evidence,
                    "license": (
                        target_license or existing_license
                        if isinstance(metadata_index, str) and metadata_index
                        else normalized["license"]
                    ),
                    "upstream_category": target_source_metadata[
                        source_path
                    ].get("category"),
                    "upstream_risk": target_source_metadata[source_path].get(
                        "risk"
                    ),
                    "upstream_source": target_source_metadata[source_path].get(
                        "source"
                    ),
                }
            )

    manifest: dict[str, object] = {
        "schema_version": 1,
        "source": normalized,
        "base_commit": base_commit,
        "path_corrections": sorted(corrections, key=lambda item: item["from"]),
        "candidates": candidates,
    }
    staging.mkdir()
    try:
        manifest_path = staging / "manifest.json"
        dump_json_atomic(manifest_path, manifest)
        review = {
            "schema_version": 1,
            "manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
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


def commit_source_update(
    root: Path, manifest_path: Path, review_path: Path
) -> dict[str, object]:
    root = Path(root)
    _require_clean_worktree(root)
    manifest_bytes = Path(manifest_path).read_bytes()
    manifest = json.loads(manifest_bytes)
    review = load_json(Path(review_path))
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "source",
        "base_commit",
        "path_corrections",
        "candidates",
    }:
        raise IntakeError("source update manifest fields are invalid")
    if manifest.get("schema_version") != 1:
        raise IntakeError("source update schema_version is invalid")
    source = validate_source_spec(manifest.get("source"))
    base_commit = manifest.get("base_commit")
    corrections = manifest.get("path_corrections")
    candidates = manifest.get("candidates")
    if (
        not isinstance(base_commit, str)
        or COMMIT.fullmatch(base_commit) is None
        or not isinstance(corrections, list)
        or not all(isinstance(item, dict) for item in corrections)
        or not isinstance(candidates, list)
        or not all(isinstance(item, dict) for item in candidates)
    ):
        raise IntakeError("source update manifest is invalid")
    candidate_paths = [candidate.get("source_path") for candidate in candidates]
    if (
        not all(isinstance(path, str) for path in candidate_paths)
        or len(candidate_paths) != len(set(candidate_paths))
        or any(
            candidate.get("change")
            not in {"added", "modified", "path-corrected"}
            for candidate in candidates
        )
    ):
        raise IntakeError("source update candidates are invalid")

    skills_payload = _load_json_object(root / "registry/skills.json")
    quarantine_payload = _load_json_object(root / "registry/quarantine.json")
    skills = skills_payload.get("skills")
    quarantine = quarantine_payload.get("records")
    if not isinstance(skills, list) or not isinstance(quarantine, list):
        raise IntakeError("registry records are invalid")
    known_ids = {
        str(record.get("skill_id"))
        for record in [*skills, *quarantine]
        if isinstance(record, dict) and isinstance(record.get("skill_id"), str)
    }
    validate_review(manifest_bytes, review, known_ids)
    decisions = review["decisions"]
    decision_by_path = {
        str(decision["source_path"]): decision for decision in decisions
    }
    for candidate in candidates:
        decision = decision_by_path[str(candidate["source_path"])]
        action = decision["decision"]
        if candidate["change"] == "path-corrected" and action != "import":
            raise IntakeError("path corrections must be imported")
        if candidate["change"] == "modified" and action not in {
            "import",
            "canonical",
            "quarantine",
        }:
            raise IntakeError(
                "modified source updates must be imported, canonicalized, or quarantined"
            )
        if candidate["change"] == "added" and action not in {
            "import",
            "canonical",
            "quarantine",
        }:
            raise IntakeError(
                "added source updates must be imported, canonicalized, or quarantined"
            )
        license_value = candidate.get("license")
        if action in {"import", "canonical"} and (
            not isinstance(license_value, str)
            or not license_value.strip()
            or license_value.strip().upper() == "UNKNOWN"
        ):
            raise IntakeError(
                "active source update requires per-skill license evidence"
            )

    with tempfile.TemporaryDirectory(prefix="asr-update-recheck-") as temporary:
        regenerated_stage = Path(temporary) / "stage"
        prepare_source_update(root, source, regenerated_stage)
        if (regenerated_stage / "manifest.json").read_bytes() != manifest_bytes:
            raise IntakeError("source update manifest no longer matches repository")

    sources_payload = _load_json_object(root / "registry/sources.lock.json")
    sources = sources_payload.get("sources")
    if not isinstance(sources, list):
        raise IntakeError("source lock records are invalid")
    source_index = next(
        (
            index
            for index, item in enumerate(sources)
            if isinstance(item, dict)
            and item.get("source_id") == source["source_id"]
        ),
        None,
    )
    if source_index is None or sources[source_index].get("commit") != base_commit:
        raise IntakeError("source update base commit does not match source lock")

    index_payload = _load_json_object(root / "librarian-index.json")
    entries = index_payload.get("entries")
    safety_payload = _load_json_object(root / "registry/safety-signals.json")
    profiles = safety_payload.get("profiles")
    if not isinstance(entries, list) or not isinstance(profiles, list):
        raise IntakeError("discovery or safety registry is invalid")

    correction_by_path: dict[str, str] = {}
    id_remap: dict[str, str] = {}
    for correction in corrections:
        if set(correction) != {"from", "skill_id", "to"} or not all(
            isinstance(correction[field], str)
            for field in ("from", "skill_id", "to")
        ):
            raise IntakeError("source path correction is invalid")
        old_path = correction["from"]
        new_path = correction["to"]
        old_id = correction["skill_id"]
        if old_path in correction_by_path or old_id in id_remap:
            raise IntakeError("source path corrections are duplicated")
        correction_by_path[old_path] = new_path
        id_remap[old_id] = stable_skill_id(source["source_id"], new_path)

    new_skills = [dict(record) for record in skills]
    new_quarantine = [dict(record) for record in quarantine]
    for record in [*new_skills, *new_quarantine]:
        if record.get("source_id") != source["source_id"]:
            if record.get("canonical_skill_id") in id_remap:
                record["canonical_skill_id"] = id_remap[record["canonical_skill_id"]]
            continue
        old_path = str(record["source_path"])
        new_path = correction_by_path.get(old_path, old_path)
        record["source_path"] = new_path
        record["skill_id"] = stable_skill_id(source["source_id"], new_path)
        record["source_commit"] = source["commit"]
        if record.get("canonical_skill_id") in id_remap:
            record["canonical_skill_id"] = id_remap[record["canonical_skill_id"]]

    new_entries = [dict(entry) for entry in entries]
    for entry in new_entries:
        if entry.get("skill_id") in id_remap:
            entry["skill_id"] = id_remap[entry["skill_id"]]
        if entry.get("canonical") in id_remap:
            entry["canonical"] = id_remap[entry["canonical"]]
    profiles_by_id: dict[str, dict[str, object]] = {}
    for profile in profiles:
        updated = dict(profile)
        if updated.get("skill_id") in id_remap:
            updated["skill_id"] = id_remap[updated["skill_id"]]
        profiles_by_id[str(updated["skill_id"])] = updated

    records_by_source_path = {
        str(record["source_path"]): record
        for record in [*new_skills, *new_quarantine]
        if record.get("source_id") == source["source_id"]
    }
    entries_by_load_name = {
        str(entry.get("flat_name")): entry for entry in new_entries
    }
    used_names = {
        str(record["load_name"])
        for record in [*new_skills, *new_quarantine]
        if isinstance(record.get("load_name"), str)
    }
    candidate_by_path = {
        str(candidate["source_path"]): candidate for candidate in candidates
    }
    replacements: list[tuple[str, Path]] = []
    additions: list[tuple[str, Path]] = []
    added_count = 0
    modified_count = 0
    quarantined_count = 0

    with tempfile.TemporaryDirectory(prefix="asr-commit-update-") as temporary:
        checkout = Path(temporary) / "source"
        checkout_pinned_source(source, checkout)
        metadata_index = sources[source_index].get("metadata_index")
        if isinstance(metadata_index, str) and metadata_index:
            target_bundles, _ = indexed_source_bundles(
                checkout, metadata_index, source["skills_root"]
            )
        else:
            target_bundles = {
                bundle.relative_to(checkout).as_posix(): bundle
                for bundle in discover_source_bundles(
                    checkout / source["skills_root"]
                )
            }

        for source_path in sorted(candidate_by_path):
            candidate = candidate_by_path[source_path]
            decision = decision_by_path[source_path]
            action = str(decision["decision"])
            canonical_target = decision["canonical_skill_id"]
            if isinstance(canonical_target, str):
                canonical_target = id_remap.get(canonical_target, canonical_target)
            existing = records_by_source_path.get(source_path)
            if existing is not None:
                entry = entries_by_load_name[str(existing["load_name"])]
                entry["skill_id"] = existing["skill_id"]
                entry["taxonomy"] = decision["taxonomy"]
                entry["category_fine"] = decision["category_fine"]
                if candidate["change"] == "modified":
                    candidate_license = candidate.get("license")
                    if isinstance(candidate_license, str) and candidate_license.strip():
                        existing["license"] = candidate_license.strip()
                    existing.update(
                        {
                            "name": candidate["name"],
                            "content_sha256": candidate["content_sha256"],
                            "risk": "unknown",
                            "risk_reasons": ["initial-review-required"],
                        }
                    )
                    entry.update(
                        {
                            "name": candidate["name"],
                            "description": candidate["description"],
                            "risk": "unknown",
                            "license": existing["license"],
                            "canonical": canonical_target,
                        }
                    )
                    existing["canonical_skill_id"] = canonical_target
                    if action == "quarantine":
                        if existing in new_skills:
                            new_skills.remove(existing)
                        if existing not in new_quarantine:
                            new_quarantine.append(existing)
                        existing.update(
                            {
                                "state": "quarantined",
                                "rule_ids": ["reviewed-update-quarantine"],
                                "disposition": "quarantined",
                                "canonical_skill_id": None,
                            }
                        )
                        entry["canonical"] = None
                        quarantined_count += 1
                    else:
                        if existing in new_quarantine:
                            new_quarantine.remove(existing)
                        if existing not in new_skills:
                            new_skills.append(existing)
                        existing.pop("rule_ids", None)
                        existing.pop("disposition", None)
                        existing["state"] = "active"
                        profiles_by_id[str(existing["skill_id"])] = {
                            "skill_id": existing["skill_id"],
                            **candidate["safety_profile"],
                        }
                    replacements.append(
                        (str(existing["catalog_path"]), target_bundles[source_path])
                    )
                    modified_count += 1
                continue

            load_name = next_load_name(
                slugify_load_name(str(candidate["name"])),
                source["source_id"],
                used_names,
            )
            used_names.add(load_name)
            catalog_path, destination = catalog_destination(
                root, str(decision["taxonomy"]), load_name
            )
            skill_id = stable_skill_id(source["source_id"], source_path)
            candidate_license = candidate.get("license")
            license_value = (
                candidate_license.strip()
                if isinstance(candidate_license, str) and candidate_license.strip()
                else "UNKNOWN"
            )
            record = {
                "skill_id": skill_id,
                "name": candidate["name"],
                "load_name": load_name,
                "catalog_path": catalog_path,
                "source_id": source["source_id"],
                "source_commit": source["commit"],
                "source_path": source_path,
                "content_sha256": candidate["content_sha256"],
                "license": license_value,
                "risk": "unknown",
                "risk_reasons": ["initial-review-required"],
                "state": "quarantined" if action == "quarantine" else "active",
                "canonical_skill_id": canonical_target,
                "first_seen_version": "0.4.0",
            }
            if action == "quarantine":
                record.update(
                    {
                        "rule_ids": ["reviewed-update-quarantine"],
                        "disposition": "quarantined",
                    }
                )
                new_quarantine.append(record)
                quarantined_count += 1
            else:
                new_skills.append(record)
            records_by_source_path[source_path] = record
            new_entries.append(
                {
                    "skill_id": skill_id,
                    "name": candidate["name"],
                    "flat_name": load_name,
                    "taxonomy": decision["taxonomy"],
                    "category_fine": decision["category_fine"],
                    "description": candidate["description"],
                    "risk": "unknown",
                    "license": license_value,
                    "canonical": canonical_target,
                }
            )
            if action != "quarantine":
                profiles_by_id[skill_id] = {
                    "skill_id": skill_id,
                    **candidate["safety_profile"],
                }
            additions.append((catalog_path, target_bundles[source_path]))
            added_count += 1

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
        new_sources = [dict(item) for item in sources]
        updated_source = dict(new_sources[source_index])
        updated_source.update(
            {
                "commit": source["commit"],
                "license_note": source["license_note"],
                "review": {
                    "status": "reviewed",
                    "artifact": artifact_relative,
                    "manifest_sha256": artifact["manifest_sha256"],
                },
            }
        )
        new_sources[source_index] = updated_source
        new_sources_payload = {**sources_payload, "sources": new_sources}
        new_skills_payload = {**skills_payload, "skills": new_skills}
        new_quarantine_payload = {**quarantine_payload, "records": new_quarantine}
        new_index_payload = {**index_payload, "entries": new_entries}
        new_index_payload["count"] = len(new_entries)
        active_ids = sorted(
            str(record["skill_id"])
            for record in new_skills
            if record.get("state") == "active"
        )
        new_safety_payload = {
            "schema_version": 1,
            "profiles": [profiles_by_id[skill_id] for skill_id in active_ids],
        }
        upstream_path = root / "registry/upstream-review.json"
        upstream_payload = _load_json_object(upstream_path)
        if upstream_payload.get("source_id") == source["source_id"]:
            upstream_payload = {
                **upstream_payload,
                "pinned_commit": source["commit"],
                "observed_commit": source["commit"],
                "records": [],
            }
        _validate_commit_objects(
            new_sources_payload, new_skills_payload, new_quarantine_payload
        )
        if not valid_safety_registry(new_safety_payload, new_skills):
            raise IntakeError("safety profile records are invalid")

        json_paths = [
            root / "registry/sources.lock.json",
            root / "registry/skills.json",
            root / "registry/quarantine.json",
            root / "librarian-index.json",
            root / "registry/safety-signals.json",
            upstream_path,
        ]
        json_payloads = [
            new_sources_payload,
            new_skills_payload,
            new_quarantine_payload,
            new_index_payload,
            new_safety_payload,
            upstream_payload,
        ]
        original_bytes = {path: path.read_bytes() for path in json_paths}
        backups: list[tuple[Path, Path]] = []
        created: list[Path] = []
        created_parents: list[Path] = []
        temporary_copies: list[Path] = []
        artifact_created = False
        try:
            backup_root = Path(temporary) / "backups"
            backup_root.mkdir()
            for backup_index, (relative, bundle) in enumerate(replacements):
                destination = root / relative
                temporary_copy = Path(
                    tempfile.mkdtemp(
                        dir=destination.parent, prefix=f".{destination.name}.new."
                    )
                )
                temporary_copies.append(temporary_copy)
                shutil.copytree(bundle, temporary_copy, dirs_exist_ok=True)
                backup = backup_root / str(backup_index)
                destination.replace(backup)
                backups.append((destination, backup))
                temporary_copy.replace(destination)
                temporary_copies.remove(temporary_copy)
            catalog_root = (root / "catalog").resolve()
            for relative, bundle in additions:
                destination = root / relative
                _create_parent_directories(
                    destination, catalog_root, created_parents
                )
                temporary_copy = Path(
                    tempfile.mkdtemp(
                        dir=destination.parent, prefix=f".{destination.name}.new."
                    )
                )
                temporary_copies.append(temporary_copy)
                shutil.copytree(bundle, temporary_copy, dirs_exist_ok=True)
                temporary_copy.replace(destination)
                temporary_copies.remove(temporary_copy)
                created.append(destination)
            for path, payload in zip(json_paths, json_payloads):
                dump_json_atomic(path, payload)
            dump_json_atomic(artifact_path, artifact)
            artifact_created = True
            report = verify_repository(root)
            if report.result != "pass":
                check_ids = sorted(
                    str(finding.get("check_id"))
                    for finding in report.findings
                )
                raise IntakeError(
                    "post-update strict verification failed: "
                    + ", ".join(check_ids)
                )
        except Exception:
            for path, content in original_bytes.items():
                _restore_bytes_atomic(path, content)
            if artifact_created:
                artifact_path.unlink(missing_ok=True)
            for temporary_copy in temporary_copies:
                shutil.rmtree(temporary_copy, ignore_errors=True)
            for destination in reversed(created):
                shutil.rmtree(destination, ignore_errors=True)
            for destination, backup in reversed(backups):
                shutil.rmtree(destination, ignore_errors=True)
                if backup.exists():
                    backup.replace(destination)
            for parent in reversed(created_parents):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            raise
        for _, backup in backups:
            shutil.rmtree(backup)

    return {
        "added": added_count,
        "modified": modified_count,
        "quarantined": quarantined_count,
        "path_corrected": len(corrections),
        "result": "pass",
        "strict_verifier": "pass",
    }
