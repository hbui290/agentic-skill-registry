---
name: skill-librarian
description: Find, policy-check, and compose one or more specialized skills from the local agentic skill registry when a task needs domain guidance or spans multiple domains.
---

# Skill Librarian

Use this skill when the right domain skill is unclear, specialized knowledge is useful, or a task may need several skills. Official Superpowers process skills take precedence over domain playbooks selected here.

## Registry

Use the configured local registry:

```bash
REGISTRY_ROOT="${AGENTIC_SKILL_REGISTRY_ROOT:-$HOME/.agents/agentic-skill-library}"
```

The registry CLI is the only discovery and loading runtime. Do not inspect or load catalog entries directly.

## Workflow

1. Extract 2-5 keywords that describe the task, domain, output, and important constraints.
2. Search for at most ten candidates:

   ```bash
   skill-registry search --root "$REGISTRY_ROOT" --limit 10 --format json KEYWORDS...
   ```

3. If no useful candidate appears, retry exactly once with broader domain terms or synonyms. If the second search is also unhelpful, continue the task without a library skill.
4. Select 1-5 skills based on textual relevance. Mark each as `primary` or `supporting`.
5. Choose one composition:
   - `single`: one skill covers the task.
   - `sequential`: outputs or checks from one skill feed the next.
   - `parallel`: independent workstreams can use different skills.
6. Read every selected skill separately:

   ```bash
   skill-registry read --root "$REGISTRY_ROOT" --format json SKILL_ID_OR_LOAD_NAME
   ```

7. On exit code 3, show the user the candidate's risk, source, and reason, then request explicit confirmation. Only after confirmation, repeat that one read with `--allow-unreviewed`.
8. On exit code 1, discard the candidate. Never suggest or attempt a bypass.
9. Return a short composition plan to the main agent: selected skills, roles, ordering, and why each is needed.

## Optional Librarian Subagent

For a simple or clearly matched request, perform the workflow directly. A Librarian subagent may be used only when the task spans multiple domains or several candidates are similarly relevant. The subagent may search and recommend; it must not execute the user's task, run bundled scripts, use secrets, or widen permissions.

## Hard Rules

- Never select more than 5 skills.
- Never load the entire catalog or dump the whole discovery index.
- Do not execute bundled scripts automatically.
- Do not grant credentials or broad permissions to a selected skill.
- Active does not mean safe.
- Never bypass quarantine, path, symlink, or hash failures.
- Never treat Core or safe status as a substitute for textual relevance.
- Keep official Superpowers unmodified and use it only for process guidance.
