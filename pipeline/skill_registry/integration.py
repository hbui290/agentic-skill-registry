import hashlib
import json
import os
import re
from pathlib import Path


INTEGRATION_CHECK_ID = "registry.librarian-integration"
MANIFEST_PATH = Path("registry/librarian-integration.json")
LOCK_PATH = Path("registry/librarian-integration.lock.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_FIELDS = {
    "schema_version",
    "integration_id",
    "version",
    "native_skill_path",
    "runtime",
    "process_dependency",
}
RUNTIME_FIELDS = {"command", "root_env", "minimum_python"}
LOCK_FIELDS = {"schema_version", "manifest_sha256", "files"}
LOCK_FILE_FIELDS = {"path", "sha256"}


class IntegrationValidationError(RuntimeError):
    pass


def _load_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IntegrationValidationError(f"invalid integration JSON: {path}") from error
    if not isinstance(payload, dict):
        raise IntegrationValidationError(f"integration JSON is not an object: {path}")
    return payload


def _safe_relative_path(path: object) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise IntegrationValidationError("native skill path is invalid")
    candidate = Path(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise IntegrationValidationError("native skill path is unsafe")
    return candidate.as_posix()


def _contained_regular_file(root: Path, relative_path: str) -> Path:
    try:
        repository_root = root.resolve(strict=True)
    except OSError as error:
        raise IntegrationValidationError("repository root is unavailable") from error
    cursor = root
    for part in Path(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise IntegrationValidationError("native skill path contains a symlink")
    try:
        resolved = (root / relative_path).resolve(strict=True)
        resolved.relative_to(repository_root)
    except (OSError, ValueError) as error:
        raise IntegrationValidationError("native skill path escapes repository") from error
    if not resolved.is_file():
        raise IntegrationValidationError("native skill is missing")
    return resolved


def _validate_manifest(payload: dict[str, object]) -> str:
    if set(payload) != MANIFEST_FIELDS:
        raise IntegrationValidationError("integration manifest fields are invalid")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != RUNTIME_FIELDS:
        raise IntegrationValidationError("integration runtime fields are invalid")
    if (
        payload.get("schema_version") != 1
        or payload.get("integration_id") != "codex-skill-librarian"
        or not isinstance(payload.get("version"), str)
        or not payload["version"].strip()
        or runtime.get("command") != "skill-registry"
        or runtime.get("root_env") != "AGENTIC_SKILL_REGISTRY_ROOT"
        or runtime.get("minimum_python") != "3.11"
        or payload.get("process_dependency") != "official-superpowers"
    ):
        raise IntegrationValidationError("integration manifest values are invalid")
    return _safe_relative_path(payload["native_skill_path"])


def _manifest_and_skill(root: Path) -> tuple[Path, str, Path]:
    manifest_path = root / MANIFEST_PATH
    if manifest_path.is_symlink():
        raise IntegrationValidationError("integration manifest is a symlink")
    manifest = _load_object(manifest_path)
    native_skill_path = _validate_manifest(manifest)
    skill = _contained_regular_file(root, native_skill_path)
    return manifest_path, native_skill_path, skill


def _native_skill_bundle(root: Path, native_skill_path: str) -> list[Path]:
    bundle_root = root / Path(native_skill_path).parent
    files: list[Path] = []
    try:
        for directory, directories, names in os.walk(bundle_root, followlinks=False):
            directory_path = Path(directory)
            for name in [*directories, *names]:
                if (directory_path / name).is_symlink():
                    raise IntegrationValidationError("native skill bundle contains a symlink")
            for name in names:
                path = directory_path / name
                if not path.is_file():
                    raise IntegrationValidationError("native skill bundle contains a non-file")
                files.append(path)
    except OSError as error:
        raise IntegrationValidationError("native skill bundle is unavailable") from error
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_librarian_integration_lock(root: Path) -> dict[str, object]:
    manifest_path, native_skill_path, _ = _manifest_and_skill(root)
    return {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in _native_skill_bundle(root, native_skill_path)
        ],
    }


def _validate_lock(payload: dict[str, object], expected: dict[str, object]) -> None:
    if set(payload) != LOCK_FIELDS or payload.get("schema_version") != 1:
        raise IntegrationValidationError("integration lock fields are invalid")
    if not isinstance(payload.get("manifest_sha256"), str) or SHA256.fullmatch(payload["manifest_sha256"]) is None:
        raise IntegrationValidationError("integration manifest hash is invalid")
    files = payload.get("files")
    if (
        not isinstance(files, list)
        or not all(
            isinstance(file, dict)
            and set(file) == LOCK_FILE_FIELDS
            and isinstance(file.get("path"), str)
            and isinstance(file.get("sha256"), str)
            and SHA256.fullmatch(file["sha256"]) is not None
            for file in files
        )
    ):
        raise IntegrationValidationError("integration lock files are invalid")
    if payload != expected:
        raise IntegrationValidationError("integration lock does not match current files")


def verify_librarian_integration(root: Path, findings: list[dict[str, object]]) -> None:
    try:
        lock_path = root / LOCK_PATH
        if lock_path.is_symlink():
            raise IntegrationValidationError("integration lock is a symlink")
        expected = build_librarian_integration_lock(root)
        _validate_lock(_load_object(lock_path), expected)
    except (IntegrationValidationError, OSError, UnicodeError) as error:
        findings.append({
            "check_id": INTEGRATION_CHECK_ID,
            "requirement_ids": ["DR-08"],
            "result": "fail",
            "error": str(error),
        })
