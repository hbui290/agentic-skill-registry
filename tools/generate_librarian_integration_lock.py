#!/usr/bin/env python3
"""Regenerate the tracked native-Librarian lock after an intentional change."""

import argparse
import json
import sys
from pathlib import Path


def _pipeline_root() -> Path:
    return Path(__file__).resolve().parents[1] / "pipeline"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    sys.path.insert(0, str(_pipeline_root()))
    from skill_registry.integration import LOCK_PATH, build_librarian_integration_lock

    root = args.root.resolve()
    lock = build_librarian_integration_lock(root)
    (root / LOCK_PATH).write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
