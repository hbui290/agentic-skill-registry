# Migration from agentic-library

The successor preserves Git history and all 1,954 legacy records. Skill content
now lives under `catalog/`. The original repository remains unchanged and is
configured as the read-only `upstream` remote. Operational updates remain
disabled until the pinned proposal pipeline is delivered.

## Source refresh and Core admission

Check whether a locked source has a newer upstream commit without changing the
catalog or lock file:

```bash
PYTHONPATH=pipeline python -m skill_registry.cli refresh --format json
```

`refresh` is reporting only. A changed upstream commit must be imported,
reviewed, and re-hashed in a separate change before `sources.lock.json` can be
updated.

`registry/core.json` is intentionally empty after migration. A skill can enter
Core only through a reviewed manifest change when it is active and its registry
risk is `safe`; strict verification rejects every other Core member:

```bash
PYTHONPATH=pipeline python -m skill_registry.cli verify --strict
```
