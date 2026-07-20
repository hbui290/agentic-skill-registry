# Adversarial QA report — 2026-07-20

## Result

The original review target was `9536bf3`. Runtime remediation was verified in PR #17 (`9328081`), and follow-up search-quality and CI hardening was verified in PR #19 (`5000eb1`).

There are no open Critical findings.

| ID | Severity | Outcome |
| --- | --- | --- |
| QA-01 | Important | Resolved: raw bundle paths and ancestors are rejected when symlinked before resolution and hash verification. |
| QA-02 | Important improvement | Mitigated and measured: description-only multi-term matches require two terms; the deterministic top-5 corpus has 23 queries. |
| QA-03 | Minor | Resolved: malformed records produce normalized CLI errors without traceback. |
| QA-04 | Minor | Resolved: README documents the stable JSON `matches` field. |

## Controls confirmed

- Strict verifier passed on the canonical clone.
- Unknown instructions were withheld: Azure read returned exit `3` with no instructions.
- Unknown hash mismatch was blocked with exit `1` before confirmation.
- Dangerous, quarantine, and nested-symlink probes were blocked.
- Focused intake/filesystem/validator tests passed (203 tests); the transaction is exception-safe.
- CI Python 3.11–3.14 and strict verification passed for PR #17 and PR #19.
- Recovery Kit confirmed the installed native Librarian matches its integration lock.

## Boundaries not claimed

- Intake is not power-loss or process-kill safe.
- The planned repeated stress-loop harness was malformed and deliberately not retried.
- The query corpus is deterministic and small, not yet a broad sample of real user requests.

## Next step

Collect real requests, add expected top-5 and no-match cases, then tune only deterministic lexical rules if measurements justify it. Do not add embeddings or a hosted service without measured need.
