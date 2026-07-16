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
from skill_registry.filesystem import dump_json_atomic, load_json
from skill_registry.identity import stable_skill_id
from skill_registry.text import jaccard, tokenize


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
