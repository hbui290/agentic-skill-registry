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

## Upstream review boundary

`registry/upstream-review.json` records the skill-level delta from the pinned
secondary source to commit `5e31f236726a988e833b39215d140b2173bf05c0`. It
contains 10 new Markdown-only candidates and 3 modified Markdown-only skills,
all kept in review. Two changed skills carry executable changes and remain
quarantined: `skills/git-pushing` and `skills/telegram-bot-messaging`.

The report is evidence, not an import queue. No reviewed entry is copied into
the catalog, promoted to Core, or allowed to change the pinned source commit
until its contents and license have been reviewed in a separate change.

Phase 5 reviewed the 10 new Markdown-only entries. Nine are marked `critical`
by upstream metadata and one is marked `offensive`; none is eligible for import
or Core admission under this registry's safety policy.
