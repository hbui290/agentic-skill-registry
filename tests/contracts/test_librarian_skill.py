from pathlib import Path

import yaml


REFERENCE_FILES = (
    "control-plane.md",
    "trust-and-safety.md",
    "composition.md",
    "decision-trace.md",
    "source-intake.md",
    "evaluation.md",
)


def _skill(repo_root: Path) -> tuple[dict[str, object], str]:
    path = repo_root / "skills/skill-librarian/SKILL.md"
    content = path.read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    return yaml.safe_load(frontmatter), body


def _bundle_body(repo_root: Path) -> str:
    _, body = _skill(repo_root)
    references = repo_root / "skills/skill-librarian/references"
    return "\n".join(
        [body] + [
            (references / name).read_text(encoding="utf-8")
            for name in REFERENCE_FILES
        ]
    )


def test_only_librarian_is_native(repo_root):
    skill_dirs = sorted(path.name for path in (repo_root / "skills").iterdir() if path.is_dir())
    assert skill_dirs == ["skill-librarian"]


def test_librarian_contract(repo_root):
    metadata, body = _skill(repo_root)
    normalized_body = " ".join(_bundle_body(repo_root).split())
    assert metadata["name"] == "skill-librarian"
    assert "specialized" in metadata["description"].lower()
    for trigger in (
        "complex",
        "unfamiliar",
        "multi-part",
        "explicitly asks for a skill or playbook",
        "specialized deliverable or non-routine domain guidance need",
        "unfamiliar domain guidance",
        "two or more independent domains",
        "skip routine work",
    ):
        assert trigger in metadata["description"].lower()

    required = [
        "AGENTIC_SKILL_REGISTRY_ROOT",
        "skill-registry search",
        "skill-registry read",
        "2-5 keywords",
        "retry exactly once",
        "1-8 domain skills",
        "Prefer 1-5",
        "primary",
        "supporting",
        "single",
        "sequential",
        "parallel",
        "exit code 1",
        "Do not execute bundled scripts",
        "Apply an applicable Official Superpowers skill for process first",
        "Librarian decision — Phase <n>",
        "Candidates:",
        "Selected:",
        "Composition:",
        "Why:",
        "Policy:",
        "Handoff:",
        "no-match",
        "## Required trigger check",
        "Before planning or execution",
        "User explicitly asks for the Librarian",
        "more than one workstream",
        "Do not invoke it for simple general reasoning",
        "Do not invoke it merely because a request mentions a tool or service",
        "task title sounds clear",
        "then invoke Librarian in the same task phase",
        "Superpowers does not replace domain-skill discovery",
        "Do not add a runtime hook, MCP integration, or automatic router",
    ]
    for phrase in required:
        assert phrase in normalized_body


def test_librarian_forbids_unsafe_shortcuts(repo_root):
    body = _bundle_body(repo_root)
    forbidden = ["superpowers-mcp", "list_skills", "mcpServers"]
    assert not any(term in body for term in forbidden)

    required = [
        "Never load the entire catalog",
        "Never load more than 8 domain skills concurrently in one phase",
        "not a limit on the total number of skills used across a multi-phase task",
        "Never bypass quarantine, path, symlink, or hash failures",
        "Do not grant credentials or broad permissions",
        "Risk labels are metadata, not an approval gate.",
    ]
    for phrase in required:
        assert phrase in body


def test_librarian_reports_a_compact_truthful_phase_status(repo_root):
    body = _bundle_body(repo_root)
    normalized_body = body.lower()

    required = [
        "Librarian P<n>: <loaded load names> (<composition>)",
        "individual `skill-registry read --format json` command that",
        "exits 0 in the current phase",
        "before substantive task execution",
        "Never claim to use, select, load, or apply a library skill",
        "Librarian: no library skill used",
    ]
    for phrase in required:
        assert phrase.lower() in normalized_body


def test_librarian_requires_current_phase_cli_evidence(repo_root):
    body = _bundle_body(repo_root)
    normalized_body = " ".join(body.replace("`", "").split())

    required = [
        "actual successful output of skill-registry search --format json in the current phase",
        "individual skill-registry read --format json command that exits 0 in the current phase",
        "Evidence: search=exit 0; reads=<skill-id: exit 0, ...>",
        "Librarian: unavailable (CLI exit <code>)",
        "sanitized first stderr line",
        "For Policy: unavailable, do not use the standard Evidence placeholder.",
        "Evidence: search=exit <code>; stderr=<sanitized first stderr line>; reads=none",
        "Never claim to use, select, load, or apply a library skill without those current-phase command results",
    ]
    assert all(item in normalized_body for item in required)


def test_librarian_routes_to_scoped_references(repo_root):
    references = repo_root / "skills/skill-librarian/references"
    for name in REFERENCE_FILES:
        reference = references / name
        assert reference.is_file()
        assert not reference.is_symlink()

    _, body = _skill(repo_root)
    route_table = "\n".join(
        line for line in body.splitlines() if line.startswith("|")
    )
    assert "phase" in route_table.lower()
    assert "reference" in route_table.lower()
    for name in REFERENCE_FILES:
        assert f"references/{name}" in route_table
    assert "Librarian decision — Phase <n>" not in body


def test_librarian_scenarios_cover_routing_boundaries(repo_root):
    scenarios = (repo_root / "docs/evaluations/2026-07-16-librarian-scenarios.md").read_text(
        encoding="utf-8"
    )

    required_headings = [
        "## 8. Explicit Librarian request",
        "## 9. Specialized file format",
        "## 10. Tool name only",
        "## 11. Direct edit",
        "## 12. Multi-domain task",
        "## 13. No match",
        "## 14. CLI failure",
        "## 15. Blocked read",
        "## 16. High-risk signal boundary",
        "## 17. Reference selection",
        "## 18. Multi-phase handoff",
    ]
    assert all(heading in scenarios for heading in required_headings)

    required_boundaries = [
        "Policy: blocked",
        "static evidence",
        "not Registry approval or a tool-level block",
        "only before the planned action exceeds scope or the high-risk signal needs confirmation",
        "always-loaded router reads only its control-plane and",
        "It reads trust, composition, source-intake, or evaluation references only when that current phase needs their guidance",
        "each phase performs a new search, selection, and read decision",
        "no earlier domain `SKILL.md` or router reference is automatically kept",
    ]
    normalized_scenarios = " ".join(scenarios.split())
    assert all(boundary in normalized_scenarios for boundary in required_boundaries)


def test_architecture_docs_keep_catalog_out_of_native_discovery(repo_root):
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    architecture = (repo_root / "docs/architecture.md").read_text(
        encoding="utf-8"
    )
    migration = (repo_root / "docs/migration-from-agentic-library.md").read_text(
        encoding="utf-8"
    )
    assert "docs/architecture.md" in readme
    for layer in ("Process", "Routing", "Trust", "Knowledge"):
        assert layer in architecture
    for text in (readme, architecture, migration):
        assert "native-install the catalog" not in text
        assert "mcpServers" not in text
        assert "list_skills" not in text


def test_readme_documents_search_json_matches_field(repo_root):
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert '"matches"' in readme
