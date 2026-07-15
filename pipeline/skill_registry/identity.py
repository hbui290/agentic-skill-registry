import hashlib
from collections import Counter
from pathlib import PurePosixPath


def stable_skill_id(source_id: str, source_path: str) -> str:
    digest = hashlib.sha256(source_id.encode() + b"\0" + source_path.encode()).hexdigest()
    return f"asr_{digest[:16]}"


def assign_load_names(paths: list[str]) -> dict[str, str]:
    names = [PurePosixPath(path).name for path in paths]
    counts = Counter(names)
    result: dict[str, str] = {}
    for path in paths:
        pure = PurePosixPath(path)
        namespace = pure.parts[1:] if pure.parts and pure.parts[0] == "catalog" else pure.parts
        result[path] = pure.name if counts[pure.name] == 1 else "--".join(namespace)
    return result
