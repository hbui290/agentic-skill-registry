import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

import yaml

from skill_registry.hashing import tree_sha256


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


class IntakeError(RuntimeError):
    pass


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
