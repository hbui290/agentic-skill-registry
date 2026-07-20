from pathlib import Path

import yaml


def _skill(repo_root: Path) -> tuple[dict[str, object], str]:
    path = repo_root / "skills/skill-librarian/SKILL.md"
    content = path.read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_only_librarian_is_native(repo_root):
    skill_dirs = sorted(path.name for path in (repo_root / "skills").iterdir() if path.is_dir())
    assert skill_dirs == ["skill-librarian"]


def test_librarian_contract(repo_root):
    metadata, body = _skill(repo_root)
    assert metadata["name"] == "skill-librarian"
    assert "specialized" in metadata["description"].lower()

    required = [
        "AGENTIC_SKILL_REGISTRY_ROOT",
        "skill-registry search",
        "skill-registry read",
        "2-5 keywords",
        "retry exactly once",
        "1-5 domain skills",
        "primary",
        "supporting",
        "single",
        "sequential",
        "parallel",
        "exit code 3",
        "--allow-unreviewed",
        "exit code 1",
        "Do not execute bundled scripts",
        "Official Superpowers process skills take precedence",
        "Librarian decision — Phase <n>",
        "Candidates:",
        "Selected:",
        "Composition:",
        "Why:",
        "Policy:",
        "Handoff:",
        "no-match",
    ]
    for phrase in required:
        assert phrase in body


def test_librarian_forbids_unsafe_shortcuts(repo_root):
    _, body = _skill(repo_root)
    forbidden = ["superpowers-mcp", "list_skills"]
    assert not any(term in body for term in forbidden)

    required = [
        "Never load the entire catalog",
        "Never load more than 5 domain skills concurrently in one phase",
        "not a limit on the total number of skills used across a multi-phase task",
        "Never bypass quarantine, path, symlink, or hash failures",
        "Do not grant credentials or broad permissions",
        "Active does not mean safe",
    ]
    for phrase in required:
        assert phrase in body


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
