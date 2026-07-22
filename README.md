# Agentic Skill Library

> A verified, on-demand skill library for AI agents — search first, read only
> what the task needs.

Agentic Skill Library keeps a large skill catalog outside an agent's default
context. The Librarian finds relevant skills for the current phase, verifies
their provenance and content hash, then returns only the selected instructions.

## Quick start on macOS

Requires Git, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
git clone --branch codex/safety-profiles \
  https://github.com/hbui290/agentic-skill-library.git \
  ~/.agents/agentic-skill-library

uv tool install --editable ~/.agents/agentic-skill-library
export AGENTIC_SKILL_REGISTRY_ROOT="$HOME/.agents/agentic-skill-library"

skill-registry verify --root "$AGENTIC_SKILL_REGISTRY_ROOT" --strict
```

The global CLI is `skill-registry`. The catalog itself is not installed into
Codex. Install only [`skills/skill-librarian`](skills/skill-librarian/) as a
native Codex skill; do not bulk-copy `catalog/` to `~/.codex/skills`.

## How it works

```text
Task → Librarian search → select a few skills → verify path + hash → read them
```

- Search works from compact metadata, not every instruction file.
- `read` validates state, path containment, symlinks, and the bundle hash.
- Each record carries source, pinned commit, license, and content hash.
- New sources follow `prepare → review → commit`; no automatic promotion.

## Everyday use

```bash
registry="$AGENTIC_SKILL_REGISTRY_ROOT"

skill-registry search --root "$registry" --limit 5 --format json "security audit"
skill-registry read --root "$registry" --format json skill-librarian
skill-registry verify --root "$registry" --strict
```

Use the Librarian for complex, unfamiliar, specialised, or multi-part work.
Skip it for a simple task or when one known native skill already covers it.

## Safety signals

Every active bundle has a cached static profile keyed to its content hash and
scanner version. A profile can report `shell`, `network`, `credential`,
`filesystem_write`, or `prompt_injection` evidence.

`scanned` means static evidence was collected; it is **not** safety approval.
`unscanned`, `stale`, and `scan_error` are conservative states. The registry
does not enforce tools, block every `unknown` skill, or create approval flows.
The consumer agent compares a planned action with task scope and asks the owner
when it would exceed scope or a high-risk signal needs confirmation.

## Current library

| Item | State |
| --- | --- |
| Active catalog records | 1,953 |
| Searchable records | 1,949 |
| Quarantined records | 2 |
| Native Codex installation | `skill-librarian` only |

## Limits by design

- Not an MCP server, marketplace, vector database, or bulk installer.
- Not tool-level capability enforcement or a permission broker.
- Not permission to execute every instruction a skill contains.

## Documentation

- [Getting started](docs/getting-started.md)
- [Trust model](docs/trust-model.md)
- [Architecture](docs/architecture.md)
- [Adding a source](docs/source-intake.md)
- [Migration guide](docs/migration-from-agentic-library.md)

## Contributors

```bash
uv run --extra dev pytest -q
uv run --extra dev skill-registry verify --root . --strict
git diff --check
```

CI runs Python 3.11–3.14. Registry and catalog changes are reviewed commits;
use `git revert` to roll them back instead of hand-editing generated registry
files.
