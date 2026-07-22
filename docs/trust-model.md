# Integrity model

The Registry CLI verifies that a selected file still matches the catalog before reading it.

## States and outcomes

| Record state | Read behavior |
| --- | --- |
| Active | Read after integrity checks |
| Dangerous | Always blocked |
| Quarantine | Always blocked |
| Inactive or missing | Blocked |

Risk labels and the legacy Core list are provenance metadata, not an approval workflow.

## Safety profiles

`read` returns compact safety metadata after the integrity checks above. The
scanner only reports static pattern matches in a bundle; a `scanned` profile,
including one with `severity: clean`, is not a safety approval or a guarantee
that the instructions are safe to follow.

| Status | Meaning | Consumer treatment |
| --- | --- | --- |
| `scanned` | A static scan completed for this bundle hash and scanner version. | Review its signals against the planned action. |
| `unscanned` | No usable cached profile is available. | Treat conservatively. |
| `stale` | The cached content hash or scanner version no longer matches. | Treat conservatively; the Registry does not rescan during `read`. |
| `scan_error` | The static scan could not produce a usable profile. | Treat conservatively. |

Profiles are cached in `registry/safety-signals.json` with the bundle's
`content_sha256` and scanner version. A bundle-content hash change or scanner
version change invalidates that cached result. The conservative runtime states
(`unscanned`, `stale`, and `scan_error`) are returned with high severity; they
are not represented as clean.

The Registry reports this metadata but does not enforce host tools, intercept
commands, or ask the owner for approval. The consumer agent owns the contextual
decision: it asks the owner only when its planned action exceeds the explicit
task scope or a high-risk signal requires confirmation. A signal that matches
an in-scope action is not, by itself, a Registry block.

## Progressive disclosure

The native Librarian is a compact, always-loaded router, not a second catalog.
It reads focused operational references only when the current phase needs them:
control-plane and receipt guidance for routing, trust guidance for safety and
scope, composition for multi-skill phases, source intake for reviewed sources,
and evaluation for release checks. This disclosure boundary reduces context; it
does not change the Registry's integrity checks or turn static signals into
tool-level enforcement.

## Integrity checks

The CLI requires the path to stay inside `catalog/`, rejects unsafe symlinks, requires `SKILL.md`, and compares the current bundle tree hash with `registry/skills.json`.

Dangerous, quarantine, inactive, path, symlink, and hash failures cannot be bypassed.

## Provenance

Each record has a source ID, pinned source commit, source path, license, and content hash. `registry/skills.json` is authoritative; `librarian-index.json` is discovery metadata only.

The native-Librarian integration lock detects drift in the one native skill. It does not replace catalog provenance, content hash, or quarantine policy.

## Duplicates

Exact Office duplicates are canonicalized in metadata, so search shows the official record only. Original catalog bytes and provenance remain preserved.
