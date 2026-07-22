# Librarian manual scenario review

Reviewed against the local registry on 2026-07-16. These are selection and
policy transcripts, not executions of bundled skill scripts.

## 1. Single skill

- Request: work with a PDF.
- Search: `pdf`.
- Selection: `pdf` as `primary`; composition `single`.
- Read result: exit code `0` after path and hash checks.
- User-visible phase status: `Librarian P1: pdf (single)`.
- Outcome: load only the selected instructions. No second skill is added merely
  because it has a different risk label.

## 2. Sequential skills

- Request: audit a repository, then write technical documentation from the
  findings.
- Search concepts: `security audit` and `technical documentation`.
- Selection: one audit skill as `primary`, then `docs-architect` as
  `supporting`; composition `sequential`.
- Outcome: policy-check each candidate separately. The documentation step only
  receives the audit output after both selected skills pass their read gates.

## 3. Parallel skills

- Request: obtain a YouTube transcript and independently prepare a spreadsheet
  summary layout.
- Search concepts: `youtube transcript` and `spreadsheet`.
- Selection: `youtube-transcript` and one spreadsheet candidate, with one
  `primary` per workstream; composition `parallel`.
- User-visible phase status: `Librarian P1: youtube-transcript + <spreadsheet skill> (parallel)`.
- Outcome: both reads require their own policy decision. Neither selected skill
  executes the other skill or receives credentials.

## 4. Active candidate

- Candidate: `youtube-transcript`.
- Read result: exit code `0` after integrity checks.
- Outcome: instructions load because `unknown` is metadata, not an approval
  gate. Bundled scripts still do not execute.

## 5. Integrity failure

- Scenario: selected skill tree differs from its registered hash.
- Verified by: `test_read_blocks_symlink_and_hash_mismatch`.
- Outcome: exit code `1`; discard the candidate. The Librarian contract forbids
  suggesting or attempting a bypass.

## 6. No candidate

- Search: `qzxvplmno nonexistentdomain`.
- Result: zero matches with successful search status.
- User-visible phase status: `Librarian: no library skill used`.
- Outcome: retry once with meaningful broader terms if available, then continue
  without a library skill. Do not dump or scan the entire catalog.

## Review conclusion

## Scenario 7 — Multi-phase task

- Scenario: audit an API, implement a remediation, then document and release it.
- Phase 1 decision: query `api security audit`; select two primary/supporting
  audit skills with `sequential` composition. Handoff: prioritized findings and
  acceptance criteria.
- Phase 2 decision: query `implementation tests remediation`; select three
  primary/supporting implementation skills with `sequential` composition.
  Handoff: tested patch and verification results.
- Phase 3 decision: query `technical documentation release`; select two
  primary/supporting documentation/release skills with `parallel` composition.
  Handoff: published-ready documentation and release checklist.
- Outcome: seven domain skills are used across the task, while every phase loads
  no more than three. Each decision records Query, Candidates, Selected,
  Composition, Why, Policy, and Handoff. No prior `SKILL.md` is automatically
  carried into the next phase.

Every scenario loads no more than eight domain skills concurrently in one phase,
prefers one to five, assigns a role and composition, loads only through
`skill-registry read`, and never automatically runs bundled scripts. Official
Superpowers process guidance remains higher priority than a selected domain
playbook and does not count against the domain-skill quota.

## 8. Explicit Librarian request

- Request: find the right skill for a security review.
- Outcome: invoke the Librarian, run JSON search, then read each selected
  candidate before a phase status can name it.

## 9. Specialized file format

- Request: repair a PDF with annotations.
- Outcome: invoke the Librarian because the work needs specialized file-format
  guidance; status requires successful current-phase search and read results.

## 10. Tool name only

- Request: use Docker.
- Outcome: do not invoke solely because Docker was mentioned. Invoke only if
  the task also needs a specialized deliverable or non-routine domain guidance.

## 11. Direct edit

- Request: rename one local variable.
- Outcome: continue without the library; report no Librarian status because it
  was not invoked.

## 12. Multi-domain task

- Request: audit an API, implement a remediation, and document the result.
- Outcome: invoke per phase, select a composition for each phase, and retain
  only the output needed for the next phase.

## 13. No match

- Request: `qzxvplmno nonexistentdomain`.
- Outcome: run one broader retry. If both successful searches have no useful
  candidate, report `Librarian: no library skill used` and trace both results
  with `Policy: no-match`.

## 14. CLI failure

- Scenario: `skill-registry search` exits nonzero.
- Outcome: do not call it a no-match or claim the registry is broken without
  the command output. Report `Librarian: unavailable (CLI exit <code>)`, set
  `Policy: unavailable`, and trace only the sanitized first stderr line.

## 15. Blocked read

- Scenario: search succeeds, but every selected candidate's
  `skill-registry read --format json` exits `1` because of an integrity or
  policy failure.
- Outcome: discard each candidate without a bypass, report `Librarian: no
  library skill used`, and record `Policy: blocked` with only the skill IDs and
  exit codes. Continue without a library skill.

## 16. High-risk signal boundary

- Scenario: a successfully read skill has `scanned` static evidence for shell
  or credential use, while the planned action either stays within or exceeds
  the owner-approved task scope.
- Outcome: the signal is evidence, not Registry approval or a tool-level block.
  The consumer agent asks the owner only before the planned action exceeds
  scope or the high-risk signal needs confirmation. An in-scope matching signal
  does not itself block the read.

## 17. Reference selection

- Scenario: a Librarian-routed implementation phase needs search and a receipt,
  but has no safety, source-intake, composition, or release concern.
- Outcome: the always-loaded router reads only its control-plane and
  decision-trace references. It reads trust, composition, source-intake, or
  evaluation references only when that current phase needs their guidance; it
  never preloads the catalog or all prior instructions.

## 18. Multi-phase handoff

- Scenario: an audit phase produces verified findings for a remediation phase,
  followed by a documentation phase.
- Outcome: each phase performs a new search, selection, and read decision. The
  handoff carries only the findings, patch evidence, or release checklist needed
  next; no earlier domain `SKILL.md` or router reference is automatically kept.
