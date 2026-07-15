import json
import subprocess
from pathlib import Path


class MigrationError(RuntimeError):
    pass


def verify_content_unchanged(root: Path, entries: list[str], source_commit: str) -> None:
    expected: dict[str, str] = {}
    for migrated in entries:
        original = migrated.removeprefix("catalog/")
        result = subprocess.run(
            ["git", "ls-tree", "-r", "-z", source_commit, "--", original],
            cwd=root,
            check=True,
            capture_output=True,
        )
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            metadata, path = record.split(b"\t", 1)
            expected[path.decode()] = metadata.split()[2].decode()
    current_pairs: list[tuple[str, str]] = []
    for migrated in entries:
        original = migrated.removeprefix("catalog/")
        directory = root / migrated
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            relative = path.relative_to(directory).as_posix()
            current_pairs.append((f"{original}/{relative}", path.relative_to(root).as_posix()))
    current_paths = [path for _, path in current_pairs]
    actual = subprocess.run(
        ["git", "hash-object", "--stdin-paths"],
        cwd=root,
        input="\n".join(current_paths) + "\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if len(actual) != len(current_pairs):
        raise MigrationError("could not hash migrated content")
    for (original, _), object_id in zip(current_pairs, actual, strict=True):
        if expected.get(original) != object_id:
            raise MigrationError(f"content changed: {original}")
    if set(expected) != {original for original, _ in current_pairs}:
        raise MigrationError("content changed: migrated file inventory differs from baseline")


def verify(root: Path) -> dict[str, int]:
    manifest = json.loads((root / "registry/migration/legacy-manifest.json").read_text())
    entries = manifest["entries"]
    if len(entries) != len(set(entries)):
        raise MigrationError("duplicate legacy path")
    missing = [entry for entry in entries if not (root / entry).is_dir()]
    if missing:
        raise MigrationError(f"missing legacy path: {missing[0]}")
    source_commit = manifest.get("source_commit")
    if source_commit:
        verify_content_unchanged(root, entries, source_commit)
    active = sum((root / entry / "SKILL.md").is_file() for entry in entries)
    return {
        "legacy": len(entries),
        "active_candidates": active,
        "markerless": len(entries) - active,
    }


if __name__ == "__main__":
    result = verify(Path(__file__).resolve().parents[1])
    print(json.dumps(result, sort_keys=True))
    if result != {"legacy": 1954, "active_candidates": 1952, "markerless": 2}:
        raise SystemExit(1)
