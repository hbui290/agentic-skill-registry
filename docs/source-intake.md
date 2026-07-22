# Reviewed source intake

Source intake imports selected skills from a public GitHub repository pinned to
an exact commit. It deliberately separates untrusted discovery from repository
mutation:

1. `prepare-source` validates and inspects the pinned source, then writes a
   deterministic manifest and review template to a staging directory. It does
   not modify `catalog/`, registry JSON, or the Librarian index.
2. A human reviews every candidate and records one decision per source path.
3. `commit-source` validates the review and mutates the repository only after
   all safety checks pass.

Each committed source has immutable review evidence in
`registry/source-reviews/`. The source lock records either a legacy reason or
the reviewed artifact path and manifest digest. The artifact records every
candidate decision with its preparation-time content hash; it has no timestamp
or reviewer field because Git author and commit time are the audit identity.

Version one accepts only public `https://github.com/<owner>/<repo>.git` URLs and
exact 40-character commit SHAs. Never point intake at a private repository,
provide credentials, enable credential helpers, or use source material that
requires private access.

## Prepare

Run preparation from a clean checkout, with staging outside the repository:

```bash
skill-registry prepare-source \
  --root "$PWD" \
  --source-id example-source \
  --url https://github.com/example/skills.git \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skills-root skills \
  --license MIT \
  --license-note "License evidence reviewed at the pinned commit" \
  --staging /tmp/example-source-intake \
  --format json
```

`commit-source` is exception-safe: if an in-process validation or write fails,
it restores only files and catalog directories created by that transaction. A
process kill or power loss still requires `git status`, strict verification,
and, if needed, a Git revert.

Preparation treats all upstream content as untrusted. It uses a constrained,
non-interactive checkout, does not run source scripts or install dependencies,
and does not change the repository. Stop if the candidate count, target path,
commit, license evidence, or source identity differs from the expected review
scope.

## Review

Edit the generated `review.json`. The `manifest_sha256` binds the review to the
exact bytes of `manifest.json`; changing or regenerating the manifest invalidates
the review. Every manifest source path must appear exactly once with a
non-empty reason and one decision:

- `import`: add a distinct reviewed candidate as an active record.
- `canonical`: add a duplicate that points to a known, non-self canonical skill.
- `quarantine`: retain a reviewed candidate in quarantine.
- `reject`: do not copy or register the candidate.

`canonical_skill_id` must be set only for `canonical`; all other decisions use
`null`. Classification and duplicate signals are review evidence, not automatic
approval, merging, deletion, or promotion. Confirm the source path, content
hash, license, taxonomy, category, duplicate evidence, and risk-sensitive
content before accepting a candidate.

## Commit

Commit requires a completely clean Git worktree:

```bash
test -z "$(git status --porcelain)"
skill-registry commit-source \
  --root "$PWD" \
  --manifest /tmp/example-source-intake/manifest.json \
  --review /tmp/example-source-intake/review.json \
  --format json
```

Before mutation, commit validates the manifest digest and complete review. It
then preflights and re-fetches the exact pinned commit, re-discovers reviewed
candidates, and requires their content hashes to match preparation. Catalog and
JSON writes, including the source-review artifact, are rolled back if mutation
or strict post-commit verification fails. Rollback removes only the newly
created artifact; it never deletes existing review evidence.

Every imported record starts at `unknown` risk with `initial-review-required`.
Risk labels are preserved as metadata; an active, non-blocked import is
readable after integrity checks.

## Pilot acceptance

The first reviewed secondary source is
`https://github.com/MicrosoftDocs/Agent-Skills.git` at commit
`e03d6ea0dab78954ca902bad9f6556cafe772515`. The reviewed pilot contained 191
root skill bundles under `skills/`: `azure-blob-storage` was imported and the
other 190 candidates were rejected as outside the pilot scope. Strict
verification passed after the import. The imported record remains `unknown`,
which does not affect routing or reading after integrity checks.

Its durable evidence is
`registry/source-reviews/microsoftdocs-agent-skills-e03d6ea0dab78954ca902bad9f6556cafe772515.json`:
191 decisions, with one import (`skills/azure-blob-storage`) and 190 rejects.

Verify discovery and repository integrity:

```bash
skill-registry search --root "$PWD" --format json azure blob storage
uv sync --locked --extra dev
uv run --no-sync python -m pytest -q
uv run --no-sync skill-registry verify --strict
git diff --check
```

The search results must include `azure-blob-storage` in the top five, and the
strict verifier must print `result=pass failed=0`.

## Update an existing source

Prepare an exact-commit delta outside the repository, review every emitted
candidate, then commit it from a clean worktree:

```bash
skill-registry prepare-update \
  --root "$PWD" \
  --source-id example-source \
  --url https://github.com/example/skills.git \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --skills-root skills \
  --license MIT \
  --license-note "License evidence reviewed at the pinned commit" \
  --staging /tmp/example-source-update \
  --format json

# Edit /tmp/example-source-update/review.json, then:
skill-registry commit-update \
  --root "$PWD" \
  --manifest /tmp/example-source-update/manifest.json \
  --review /tmp/example-source-update/review.json \
  --format json
```

For sources with a metadata index, that index is the candidate allowlist and
may include nested skills. Per-skill license values come from that pinned index,
not from a source-wide default. An addition without license evidence cannot be
activated; retain it as `quarantine` with `UNKNOWN` license until a later pinned
index supplies evidence.

Existing records are reconciled by exact source path first, then by the stable
index ID only when repairing a legacy flattened path. Path corrections must be
imported. Modified bundles may be imported, canonicalized, or quarantined;
additions have the same three choices. `reject` is intentionally unavailable
for updates because advancing the source lock while omitting an indexed bundle
would make the next update impossible to reconcile. Quarantine therefore acts
as the durable, non-runnable record for a reviewed but unapproved bundle.

Unchanged Git tree objects are not recopied, so unchanged upstream symlinks are
never followed. A new or modified bundle containing a symlink is rejected.
Existing oversized bundles may update only when neither file count nor byte
count grows; the normal intake limits are not raised. Source deletion updates
are currently rejected rather than inferred.

## Rollback

Keep each reviewed source import in one dedicated Git commit. Roll back the
entire import with `git revert <import-commit-sha>`. Do not manually delete the
catalog bundle or edit registry/catalog/index JSON independently; those files
form one reviewed snapshot and must move together.
