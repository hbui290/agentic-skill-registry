# Getting started

This guide is for operating Agentic Skill Library locally. Read the
[README](../README.md) first for the product overview.

## 1. Install the local CLI

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

Expected output includes:

```text
result=pass failed=0
```

## 2. Install the one native skill

Install only [`skills/skill-librarian`](../skills/skill-librarian/) using
OpenAI's `$skill-installer`. Do not install `catalog/` into
`~/.codex/skills`; catalog skills remain repository data until the Librarian
selects one for a task.

The compact Librarian router is the only always-loaded native material. It reads
its focused references just in time for the current phase; it does not preload
the catalog, every reference, or prior phase instructions.

## 3. Search without loading instructions

```bash
skill-registry search \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --limit 10 --format json \
  youtube transcript
```

Search returns metadata only. The JSON response is:

```json
{"query": "youtube transcript", "matches": []}
```

No-match is valid: the command exits `0` and the agent can continue without a
library skill.

## 4. Read a selected active skill

Any selected active skill can be read directly:

```bash
skill-registry read \
  --root "$AGENTIC_SKILL_REGISTRY_ROOT" \
  --format json moyu
```

Before returning instructions, the CLI checks:

```text
record state
→ quarantine and dangerous policy
→ catalog path containment and SKILL.md
→ symlink safety and tree hash
```

`unknown`, `review`, and `safe` are catalog metadata, not a confirmation gate.
Quarantine, inactive, dangerous, path, symlink, and hash failures always exit
`1` and cannot be bypassed.

The JSON form of `read` also includes compact `safety` metadata. `scanned`
means a static scan found the listed signals for the current bundle hash and
scanner version; it is not a safety approval. The other statuses are
conservative: `unscanned` has no usable cached profile, `stale` has a mismatched
bundle hash or scanner version, and `scan_error` could not produce a usable
profile. These states are returned with high severity and are never presented
as clean. `read` does not rescan a stale bundle.

Profiles are cached in `registry/safety-signals.json` and tied to the bundle's
content hash and scanner version. The Registry reports the result; it does not
enforce shell, network, credential, or filesystem tools and does not ask for
approval. The consumer agent asks the owner only if its planned action is
outside the explicit task scope or a high-risk signal requires confirmation.

## 5. Let the Librarian route a task

Invoke Librarian on demand before planning or execution for an explicit
skill/playbook request, specialized or unfamiliar domain guidance, a named
specialist deliverable/tool, or two or more independent domains. Apply an
Official Superpowers process skill first when relevant, then invoke Librarian
in the same phase if one of those triggers applies. Skip routine work already
fully governed by installed or project-local instructions; mentioning a tool or
service alone is not a trigger.

The Librarian searches up to ten candidates and may retry once with broader
terms. It selects up to eight domain skills concurrently for a phase, prefers
one to five, assigns them `primary` or `supporting` roles, and chooses `single`,
`sequential`, or `parallel` composition.

It reads only the minimum router reference for that phase: control-plane and
receipt guidance for routing, safety guidance when signals or scope matter,
composition for multi-skill work, source intake for reviewed sources, and
evaluation for pressure tests or release checks.

A large task can have additional phases. Each new phase gets a new search and
selection; only the prior phase's needed output is handed forward. It does not
keep every earlier `SKILL.md` in context.

Official Superpowers process skills are separate from the domain-skill quota.

## Next references

- [Architecture](architecture.md)
- [Trust model](trust-model.md)
- [Source intake](source-intake.md)
