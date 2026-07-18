from __future__ import annotations

import stat
from pathlib import Path

from skill_registry.filesystem import write_bytes_atomic


def test_write_bytes_atomic_preserves_existing_mode(tmp_path: Path):
    path = tmp_path / "existing.json"
    path.write_bytes(b"old")
    path.chmod(0o640)

    write_bytes_atomic(path, b"new")

    assert path.read_bytes() == b"new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_bytes_atomic_uses_default_mode_for_new_file(tmp_path: Path):
    path = tmp_path / "new.json"

    write_bytes_atomic(path, b"new")

    assert stat.S_IMODE(path.stat().st_mode) == 0o644
