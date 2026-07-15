# Migration from agentic-library

The successor preserves Git history and all 1,954 legacy records. Skill content
now lives under `catalog/`. The original repository remains unchanged and is
configured as the read-only `upstream` remote. Operational updates remain
disabled until the pinned proposal pipeline is delivered.

## Runtime replacement

The old flattened skill directory and third-party Superpowers MCP bridge are
retired. They made discovery look like installation and did not provide the
registry's state, risk, path, or hash enforcement.

The supported runtime now has three explicit parts:

1. `skill-registry search` finds candidates from the local discovery index and
   authoritative registry records.
2. `skill-registry read` enforces policy and returns only one selected
   `SKILL.md` at a time.
3. `skills/skill-librarian` is the only native skill installed from this repo;
   it chooses and composes 1–5 domain playbooks for the main agent.

Official Superpowers remains separately installed from Obra/OpenAI and is not
forked, patched, or routed through this repository. It owns process guidance;
the Librarian owns domain-skill selection.

Set the canonical clone root before using the runtime:

```bash
export AGENTIC_SKILL_REGISTRY_ROOT="$HOME/.agents/agentic-skill-registry"
```

Do not install the complete `catalog/`, expose it as a native skill directory,
or automatically execute scripts shipped beside a selected `SKILL.md`.

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

## First Core member

Phase 6 promoted only `moyu` to Core after a content audit: its source
frontmatter is `safe`, its license is MIT, it declares no tools, contains only
documentation, and contains no network or credential instructions. No other
skill was promoted merely to make the Core list longer.
