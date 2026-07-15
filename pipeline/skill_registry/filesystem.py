import json
import os
from pathlib import Path


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
