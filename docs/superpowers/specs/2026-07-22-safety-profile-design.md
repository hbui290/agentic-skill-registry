# Skill Safety Profile Design

**Status:** approved design; pending implementation-plan review

## Goal

Expose a compact, deterministic safety profile whenever the registry reads a
skill, without blocking the library's normal on-demand workflow or rescanning
unchanged skill bundles.

## Non-goals

- Certifying a skill as safe.
- Blocking every skill with an `unknown` risk label.
- Enforcing host permissions, intercepting shell commands, or adding an MCP
  server, hook, service, or third-party scanner dependency.
- Auditing every catalog skill manually.

## Design

### 1. Scan once at catalog admission

`prepare-source` and `commit-source` derive a safety profile for each candidate
bundle. The profile is tied to the bundle's existing `content_sha256` and a
scanner version. A changed bundle hash or scanner version invalidates the
stored profile and requires a fresh scan.

The scanner uses only Python standard-library parsing and text rules. It
inspects `SKILL.md` plus all regular files in the skill bundle. It does not
execute bundled scripts, fetch URLs, or read outside the bundle.

Pattern matches are evidence, not a verdict. In particular, a security skill
may discuss injection or credential theft without performing it. A
`prompt_injection` signal is emitted only for a direct imperative override
pattern; quoted or explanatory references remain evidence only.

### 2. Store profiles separately from skill identity records

Store generated profiles in `registry/safety-signals.json`; do not add a field
to every record in `registry/skills.json`.

Each profile is keyed by `skill_id` and contains:

```json
{
  "content_sha256": "<existing bundle hash>",
  "scanner_version": 1,
  "signals": ["network", "shell"],
  "severity": "medium",
  "evidence": [
    {"path": "SKILL.md", "line": 42, "rule": "network-command"}
  ]
}
```

Allowed signals are `shell`, `network`, `credential`, `filesystem_write`, and
`prompt_injection`. Severity is `clean`, `low`, `medium`, or `high`. Stored
profiles have `status` `scanned` or `scan_error`; runtime derives `unscanned`
or `stale` when a profile is absent or does not match the record hash/scanner
version.

`evidence` is retained in the registry but is not expanded into ordinary
runtime output. Its path and line allow a reviewer to inspect a finding
without re-scanning the full bundle.

### 3. Add profile verification to strict validation

The strict verifier validates schema, unique `skill_id`, recognized signal and
severity values, hash equality with the corresponding skill record, and
scanner version. Missing or stale profiles fail strict verification once the
migration is complete.

The same type checks used by runtime for skill records are moved into strict
verification, so a strict pass cannot accept a record that later crashes
`search` or `read`.

Profiles do not replace quarantine, source provenance, containment, symlink,
or hash checks. Those existing gates remain mandatory before a skill is read.

### 4. Return a compact profile from `read`

After the existing runtime checks pass, `read_skill` returns the current safety
profile with the existing skill metadata and instructions. The profile is
metadata, not a permission decision and never changes an `active` skill to
blocked by itself.

If a profile is missing, stale, or has `scan_error`, runtime returns its
derived status and never represents the skill as clean. The caller must treat
that state conservatively for sensitive actions.

Normal response shape:

```json
{
  "skill": {"skill_id": "..."},
  "instructions": "...",
  "safety": {"signals": ["shell"], "severity": "low"}
}
```

### 5. Agent decision policy

The consumer agent compares the returned signals with the action it is about to
take and the user's explicit task scope.

- A matching action proceeds: e.g. a test task may use a `shell` skill.
- An action outside the task's scope pauses for confirmation: e.g. a writing
  task whose selected skill wants network access or credential reads.
- A high-severity `prompt_injection` signal always pauses for confirmation.

The registry reports facts; the agent owns the contextual confirmation decision.
This keeps the registry useful for one-person workflows while leaving true
tool-level enforcement as a later, separate project.

## Migration

1. Add the scanner, profile model, JSON loader, and strict-verifier rules.
2. Run a deterministic migration over current active records to produce
   `registry/safety-signals.json`.
3. Review only high-severity findings for false positives; the scanner must not
   silently quarantine or deactivate records.
4. Add the profile to runtime `read` output.
5. Update documentation with the distinction between a safety signal and a
   safety guarantee.

## Acceptance criteria

- A profile is deterministic for an unchanged bundle.
- A modified bundle hash makes the corresponding profile invalid.
- Strict verification fails on missing, malformed, duplicate, unknown-signal,
  stale-hash, or stale-version profile records.
- `read` keeps all existing containment/hash/quarantine behaviour and adds only
  compact safety metadata.
- The migration covers every active skill record without executing catalog code.
- Tests cover clean, low, medium, high, false-positive discussion text, stale
  profile, and runtime response cases.

## Deferred work

- Tool-level capability enforcement.
- UserPromptSubmit hooks.
- Semantic/LLM safety classification.
- Automatic approval or rejection based solely on risk labels.
