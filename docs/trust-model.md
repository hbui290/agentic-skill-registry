# Trust model

Agentic Skill Library separates “this file is in the catalog” from “it is safe
to read for this task.” Search finds candidates; the Registry CLI independently
enforces the permission to read one.

## States and outcomes

| Record state | Read behavior |
| --- | --- |
| Active + safe | Read after integrity checks |
| Core | Read after integrity checks; Core does not make an unrelated result relevant |
| Active + unknown/review | Ask for explicit approval; exit `3` before instructions are returned |
| Dangerous | Always blocked |
| Quarantine | Always blocked |
| Inactive or missing | Blocked |

`active` means the registry can account for a record structurally. It is not a
security audit. New imports begin as `unknown`, outside Core.

## Integrity checks

Before confirmation or read, the Registry CLI verifies the catalog path stays
inside `catalog/`, rejects unsafe symlinks, requires `SKILL.md`, and compares
the current bundle tree hash with the locked hash in `registry/skills.json`.

This means approval is never used to override a damaged or relocated skill.
Dangerous, quarantine, inactive, path, symlink, and hash failures cannot be
bypassed by `--allow-unreviewed`.

## Provenance

Each record records a source ID, pinned source commit, source path, license,
and content hash. The authoritative record is `registry/skills.json`;
`librarian-index.json` is searchable discovery metadata only.

The native-Librarian integration lock detects drift in the one native skill.
It does not replace catalog provenance, hash, risk, Core admission, or
quarantine policy.

## Duplicates

Exact Office duplicates are canonicalized in metadata, so search shows the
official record only. Original catalog bytes and provenance remain preserved.
Similar-but-not-identical skills are not automatically deleted or trusted; they
remain review decisions.
