# Agentic Skill Library

> A verified, on-demand skill library for AI agents.

This repository lets an agent find and load only the skill instructions needed
for the current task. It is not an agent application, an MCP server, or a
package that installs thousands of skills into Codex.

```text
User request
→ Agent
→ Skill Librarian searches the registry
→ selects 1–5 relevant domain skills per phase
→ policy and integrity checks
→ reads only the selected SKILL.md files
→ Agent executes the task
```

Think of `catalog/` as a library and `skill-librarian` as the librarian.
Catalog skills stay out of native Codex discovery; only the Librarian is
installed natively.

## What this repository does

- Preserves skill snapshots with source, pinned commit, license, risk state,
  content hash, and review metadata.
- Searches skills offline without loading their instructions into context.
- Reads a selected skill only after state, risk, path, symlink, and hash checks.
- Lets the Librarian combine up to five domain skills per phase as `single`,
  `sequential`, or `parallel` work.
- Imports new public GitHub sources through a review-gated
  `prepare → review → commit` process.

## What it does not do

- It does not bulk-install the catalog into Codex.
- Automatic bulk import is disabled.
- It does not treat every active skill as safe.
- It does not run bundled scripts, grant credentials, or widen permissions.
- It does not use MCP, embeddings, vector databases, hosted search, or a
  marketplace in V1.
- It does not replace Official Superpowers. Superpowers owns process guidance;
  the Librarian selects domain playbooks.

## Current status

| Item | Status |
| --- | --- |
| Active catalog entries | 1,953 |
| Searchable entries | 1,949 |
| Quarantined entries | 2 (`SPDD`, `linear`) |
| Audited Core skills | 1 (`moyu`) |
| Native skill from this repo | `skill-librarian` only |
| Secondary-source pilot | `azure-blob-storage` (still `unknown`) |

`active` means the record is structurally valid and available to the registry.
It does **not** mean the skill is safe to load without review.

## Quick start

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

Expected result:

```text
result=pass failed=0
```

Install only [`skills/skill-librarian`](skills/skill-librarian/) with OpenAI's
`$skill-installer`. Do **not** install `catalog/` into `~/.codex/skills`.

## Use skills on demand

### Search

Search returns metadata only; it never loads `SKILL.md` instructions.

```bash
skill-registry search \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --limit 10 --format json \
  youtube transcript
```

Search excludes inactive, dangerous, and canonical-duplicate records. Ranking
is deterministic and based on textual relevance first; safe/Core is only a
small bonus after a relevant match.

### Read a safe skill

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json moyu
```

Before returning instructions, the CLI verifies:

```text
record state
→ quarantine and dangerous policy
→ catalog path containment and SKILL.md
→ symlink safety and tree hash
→ risk confirmation when required
```

### Unknown or review skill

An intact `unknown` or `review` skill returns exit code `3`. It returns source,
license, risk reason, and hash metadata, but never instructions.

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json youtube-transcript
echo "$?" # 3: confirmation required
```

After a user explicitly approves that one candidate, repeat with:

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json --allow-unreviewed youtube-transcript
```

`--allow-unreviewed` does not bypass dangerous, quarantine, inactive, path,
symlink, or hash-failure policy. Those cases always return exit code `1`.

## How the Librarian works

The Librarian:

1. Extracts 2–5 keywords from the task and constraints.
2. Searches at most ten candidates, with one optional broader retry.
3. Selects at most five relevant domain skills per phase and assigns
   `primary`/`supporting` roles.
4. Chooses `single`, `sequential`, or `parallel` composition.
5. Reads each selection through the CLI policy gate.

For a large task, the Librarian can begin a new phase after passing forward only
the previous phase's necessary output and decision. The next phase searches and
loads its own batch; it does not keep old `SKILL.md` files in context. The
five-skill limit is concurrent and per phase, not a limit on the total skills a
task may use. Official Superpowers process skills are separate from this domain
skill quota.

6. Asks before loading an `unknown` or `review` candidate.

It never scans the entire catalog into context, executes bundled scripts, or
uses secrets. If no useful candidate appears after two searches, the agent
continues without a library skill.

See [the architecture contract](docs/architecture.md) for the separate Process,
Routing, Trust, and Knowledge layers.

## Add a new source

The catalog can grow from more than one source, but imports are deliberately
review-gated:

```text
prepare-source → human review → commit-source
```

Only public GitHub HTTPS repositories pinned to a full commit SHA and backed by
license evidence are supported in V1. Preparation does not mutate the
registry. Commit verifies the manifest, a clean worktree, source pin, paths,
and content hashes. Newly imported skills always start as `unknown` and outside
Core.

See [docs/source-intake.md](docs/source-intake.md) for the full operator runbook.

## Refresh pinned sources

```bash
skill-registry refresh --format json
```

This is read-only: it reports whether pinned upstream commits are current,
behind, retired, or unreachable. It never downloads, overwrites, imports, or
promotes catalog content.

## Trust model

| State | Meaning |
| --- | --- |
| Catalog | Preserved source material; may need review |
| Active | Available in the registry; not automatically safe |
| Unknown/review | Requires explicit confirmation before instructions are read |
| Quarantine | Blocked pending remediation |
| Dangerous | Blocked absolutely |
| Core | Explicitly audited allow-list; still must be relevant |

Four exact Office duplicates are metadata-canonicalized (`docx`, `pdf`,
`pptx`, `xlsx` now resolve to their `*-official` records). Original catalog
bytes and provenance remain preserved.

## Development and verification

```bash
python -m pytest -q
skill-registry verify --strict
git diff --check
```

CI runs the test suite on Python 3.11–3.14 and runs the strict verifier.

## Repository layout

| Path | Purpose |
| --- | --- |
| `catalog/` | Preserved skill snapshots; not native-installable |
| `registry/skills.json` | Authoritative identity, provenance, risk, path, and hash records |
| `registry/sources.lock.json` | Pinned source and license records |
| `registry/core.json` | Audited Core allow-list |
| `registry/quarantine.json` | Blocked records |
| `librarian-index.json` | Discovery description, taxonomy, and category data |
| `pipeline/skill_registry/` | CLI, search, policy, intake, refresh, and verifier code |
| `skills/skill-librarian/` | The only skill intended for native installation |
| `docs/` | Migration and source-intake runbooks |

## Rollback

Use `git revert` for a code or import change. Do not hand-edit catalog or
registry files to simulate a rollback. Uninstalling the Librarian affects only
this integration; Official Superpowers remains untouched.
