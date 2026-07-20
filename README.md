# Agentic Skill Library

> Give an AI agent a large skill library without dumping the whole library into
> its context.

Agentic Skill Library is an on-demand, verified library for AI-agent skills.
It helps an agent find the few instructions relevant to the work in front of
it, check whether they are trustworthy enough to open, then use only those
instructions.

It is built for people who want the breadth of a large skill collection without
the token cost, conflicting guidance, or supply-chain blind spots of installing
everything at once.

## The idea in one minute

Most skill collections answer: “Here are thousands of files. Install them.”

This project answers: “Keep the files in a library. Let a Librarian find the
right ones only when needed.”

```text
Your request
→ AI agent
→ Librarian searches the library
→ chooses the relevant skills for this phase
→ verifies policy and file integrity
→ reads only the selected instructions
→ agent does the work
```

The `catalog/` is the library shelf. It is not installed into Codex. The only
native skill from this repository is `skill-librarian`.

## Why it exists

Large skill packs are useful, but installing all of them creates practical
problems:

- Too many instructions consume context and can conflict with each other.
- A file being present does not prove it is safe or appropriate.
- Skills change upstream; it should be possible to know where a copy came from.
- An agent needs a repeatable way to select several complementary skills for a
  complex task.

Agentic Skill Library keeps the useful part of a large catalog while adding
selection, provenance, and integrity checks.

## What you get

- **On-demand routing:** the Librarian searches metadata first, then reads only
  the selected `SKILL.md` files.
- **Small working context:** up to five domain skills are loaded at the same
  time in one phase. A larger task may use new batches in later phases.
- **Trust checks before reading:** state, risk, source, file path, symlink, and
  content-hash checks happen before instructions are returned.
- **Traceable sources:** every catalog record has a source, pinned commit,
  license, and content hash.
- **Controlled growth:** new public GitHub sources go through
  `prepare → review → commit`; they are never silently imported or promoted.

## Current library

| | Current state |
| --- | --- |
| Catalog entries | 1,953 active records |
| Searchable records | 1,949 (exact duplicates are hidden from search) |
| Audited Core | 1 skill |
| Quarantine | 2 blocked records |
| Native installation | `skill-librarian` only |

Important: `active` means a record is structurally valid. It does **not** mean
the instructions have been audited as safe. Unreviewed skills require explicit
approval before their instructions can be read.

## Start here

Requirements: Python 3.11+ and Git.

```bash
git clone https://github.com/hbui290/agentic-skill-library.git \
  ~/.agents/agentic-skill-library
cd ~/.agents/agentic-skill-library
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

export AGENTIC_SKILL_REGISTRY_ROOT="$HOME/.agents/agentic-skill-library"
skill-registry verify --strict
```

Then install only [`skills/skill-librarian`](skills/skill-librarian/) using
OpenAI's `$skill-installer`. Do **not** install `catalog/` into
`~/.codex/skills`.

For the complete first-use walkthrough, including search, confirmation, and
safe reading, see [Getting started](docs/getting-started.md).

## What this is not

- Not a complete agent application or agent framework.
- Not an MCP server, marketplace, vector database, or bulk installer.
- Automatic bulk import is deliberately disabled.
- Not permission to run every catalog skill automatically.
- Not a replacement for Official Superpowers: Superpowers guides the process;
  the Librarian selects domain-specific playbooks.

## Learn more

- [Getting started](docs/getting-started.md) — install, search, read, and
  confirmation behavior.
- [Architecture](docs/architecture.md) — Process, Routing, Trust, and
  Knowledge layers.
- [Trust model](docs/trust-model.md) — what `active`, `unknown`, Core, and
  quarantine mean.
- [Adding a source](docs/source-intake.md) — reviewed multi-source intake.
- [Migration guide](docs/migration-from-agentic-library.md) — moving from the
  previous library design.

## For contributors

```bash
python -m pytest -q
skill-registry verify --strict
skill-registry refresh --format json
git diff --check
```

CI runs the suite on Python 3.11–3.14. JSON search output uses the stable shape
`{"query": "...", "matches": [...]}`; search metadata never includes skill
instructions.

Changes to the catalog or registry are reviewed changes. Use `git revert` to
roll back a committed change—do not hand-edit catalog and registry files to
simulate a rollback.
