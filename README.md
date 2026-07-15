# Agentic Skill Registry

A verified, source-aware registry for AI agent skills.

This repository keeps a large legacy catalog usable without pretending that
every entry is safe. Each skill has an identity, provenance, content hash,
license metadata, risk state, and review history.

## Current status

- 1,954 legacy records preserved.
- 1,952 active catalog entries.
- 2 markerless entries quarantined: `SPDD` and `linear`.
- 1 audited Core skill: `moyu`.
- Strict offline verification available.
- Read-only upstream freshness checks available.
- Offline on-demand search and policy-gated reads available.
- One installable Librarian skill; catalog skills stay outside native discovery.
- Automatic bulk import and automatic Core promotion are disabled.

Core is deliberately small. A skill enters Core only after its content,
license, capabilities, provenance, and risk have been reviewed.

## Quick start

Requirements: Python 3.11 or newer and Git.

```bash
git clone https://github.com/hbui290/agentic-skill-registry.git \
  ~/.agents/agentic-skill-registry
cd ~/.agents/agentic-skill-registry

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

export AGENTIC_SKILL_REGISTRY_ROOT="$HOME/.agents/agentic-skill-registry"
skill-registry verify --strict
```

A successful verification prints:

```text
result=pass failed=0
```

## Use skills on demand

Search does not load skill instructions into the agent context:

```bash
skill-registry search \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --limit 10 --format json \
  youtube transcript
```

Read performs the state, risk, path, symlink, and content-hash policy checks,
then returns only metadata and the selected `SKILL.md`:

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json moyu
```

A skill with `unknown` or `review` risk returns exit code `3` without its
instructions. After the user reviews the reported source and risk and gives
explicit approval, read that one candidate again:

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json --allow-unreviewed youtube-transcript
```

Dangerous, quarantined, inactive, escaped-path, unsafe-symlink, and hash-failed
skills are blocked and have no override.

Install only `skills/skill-librarian` with OpenAI's `$skill-installer`. The
Librarian searches for up to ten candidates, chooses 1–5 relevant skills, and
loads each one through the policy gate. Do not install `catalog/` into Codex.
Official Superpowers remains a separate, unmodified process plugin.

## Check upstream freshness

This command compares the pinned source commits with the current upstream
commits:

```bash
skill-registry refresh --format json
```

It is read-only. It does not download, overwrite, promote, or change the
catalog. A newer upstream commit must be reviewed and imported in a separate
change before the source lock is updated.

## Repository layout

| Path | Purpose |
| --- | --- |
| `catalog/` | Skill content preserved in the successor repository |
| `registry/skills.json` | Identity, provenance, hash, license, risk, and state for each skill |
| `registry/core.json` | Explicit allow-list of audited Core skill IDs |
| `registry/quarantine.json` | Entries blocked from normal use pending remediation |
| `registry/upstream-review.json` | Evidence and decisions for upstream changes |
| `pipeline/skill_registry/` | Registry models, hashing, discovery, refresh, and verification |
| `skills/skill-librarian/` | The only skill intended for native installation |
| `tests/` | Unit, contract, migration, and integration tests |
| `legacy/` | Disabled compatibility and migration material |

## Safety model

The catalog and Core are different things:

- Catalog: preserved material that may still need review.
- Active: structurally valid and available in the registry; not automatically safe.
- Review: requires human or targeted technical review before promotion.
- Quarantined: blocked because a required contract is missing or a risk rule fired.
- Core: explicitly audited and allowed for trusted default use.

Do not copy an entire upstream repository into this catalog. Upstream changes
may include plugins, workflows, web applications, scripts, or dependency
changes—not just skill instructions.

## Development

Run the complete test suite:

```bash
python -m pytest -q
```

Run the strict registry contract:

```bash
skill-registry verify --strict
```

The strict verifier checks registry structure, skill frontmatter, identities,
load names, content hashes, provenance, aliases, quarantine, Core membership,
source review records, and exceptions.

## Non-goals

This repository is not:

- a guarantee that every catalog entry is safe;
- an automatic installer for arbitrary third-party skills;
- a server, hosted search service, or replacement for agent process skills;
- a replacement for reviewing code, scripts, credentials, or external actions;
- a license to update pinned sources without importing and verifying content.

For the migration boundary and operating rules, see
[docs/migration-from-agentic-library.md](docs/migration-from-agentic-library.md).
