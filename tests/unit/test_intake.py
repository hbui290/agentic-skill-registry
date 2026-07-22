import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from skill_registry import intake
from skill_registry import filesystem
from skill_registry.identity import stable_skill_id
from skill_registry.integration import build_librarian_integration_lock
from skill_registry.intake import (
    IntakeError,
    checkout_pinned_source,
    discover_source_bundles,
    inspect_bundle,
    parse_skill_frontmatter,
    preflight_source_tree,
    validate_source_spec,
)


def valid_source_spec(**changes):
    value = {
        "source_id": "new-source",
        "url": "https://github.com/example/skills.git",
        "commit": "c" * 40,
        "skills_root": "skills",
        "license": "MIT",
        "license_note": "Fixture source license",
    }
    value.update(changes)
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def make_skill(parent, name, extra_files=None):
    bundle = parent / name
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use {name}.\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    for relative, content in (extra_files or {}).items():
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return bundle


def discovery(name, taxonomy, category, description, skill_id=None):
    value = {
        "name": name,
        "flat_name": name,
        "taxonomy": taxonomy,
        "category_fine": category,
        "description": description,
    }
    if skill_id is not None:
        value["skill_id"] = skill_id
    return value


def candidate(**changes):
    value = {
        "source_id": "new-source",
        "source_path": "skills/new-skill",
        "name": "new-skill",
        "load_name": "new-skill",
        "description": "Review Python tests",
        "content_sha256": "b" * 64,
    }
    value.update(changes)
    return value


def existing(**changes):
    value = {
        "skill_id": "asr_existing",
        "source_id": "existing-source",
        "source_path": "skills/existing",
        "load_name": "existing-skill",
        "content_sha256": "a" * 64,
    }
    value.update(changes)
    return value


def repository_digest(root):
    digest = hashlib.sha256()
    paths = list((root / "registry").rglob("*")) + list((root / "catalog").rglob("*"))
    paths.append(root / "librarian-index.json")
    for path in sorted(
        (item for item in paths if item.is_file()), key=lambda item: item.as_posix()
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def valid_root(tmp_path):
    root = tmp_path / "repo"
    (root / "catalog").mkdir(parents=True)
    source = {
        "source_id": "existing-source",
        "url": "https://github.com/example/existing.git",
        "commit": "a" * 40,
        "layout": "skills-subdir",
        "skills_root": "skills",
        "metadata_index": "",
        "license_note": "Fixture source license",
        "status": "active",
        "refreshable": True,
        "timeout_seconds": 15,
        "review": {
            "status": "legacy",
            "reason": "predates-reviewed-intake",
        },
    }
    write_json(
        root / "registry/sources.lock.json",
        {"schema_version": 1, "sources": [source]},
    )
    write_json(root / "registry/skills.json", {"schema_version": 1, "skills": []})
    write_json(
        root / "registry/safety-signals.json",
        {"schema_version": 1, "profiles": []},
    )
    write_json(
        root / "registry/quarantine.json", {"schema_version": 1, "records": []}
    )
    write_json(root / "registry/aliases.json", {"schema_version": 1, "aliases": []})
    write_json(root / "registry/core.json", {"schema_version": 1, "skill_ids": []})
    write_json(
        root / "registry/exceptions.json", {"schema_version": 1, "exceptions": []}
    )
    write_json(
        root / "registry/risk-overrides.json",
        {"schema_version": 1, "overrides": []},
    )
    write_json(root / "registry/schema-version.json", {"schema_version": 1})
    write_json(
        root / "registry/upstream-review.json",
        {
            "schema_version": 1,
            "source_id": "existing-source",
            "pinned_commit": "a" * 40,
            "observed_commit": "a" * 40,
            "records": [],
        },
    )
    write_json(
        root / "librarian-index.json",
        {"schemaVersion": 1, "count": 0, "entries": []},
    )
    librarian = root / "skills/skill-librarian/SKILL.md"
    librarian.parent.mkdir(parents=True)
    librarian.write_text(
        "---\nname: skill-librarian\ndescription: Fixture Librarian\n---\n",
        encoding="utf-8",
    )
    write_json(
        root / "registry/librarian-integration.json",
        {
            "schema_version": 1,
            "integration_id": "codex-skill-librarian",
            "version": "1.0.0",
            "native_skill_path": "skills/skill-librarian/SKILL.md",
            "runtime": {
                "command": "skill-registry",
                "root_env": "AGENTIC_SKILL_REGISTRY_ROOT",
                "minimum_python": "3.11",
            },
            "process_dependency": "official-superpowers",
        },
    )
    write_json(
        root / "registry/librarian-integration.lock.json",
        build_librarian_integration_lock(root),
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "fixture@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Fixture"], check=True
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "fixture"], check=True
    )
    return root


@pytest.fixture
def fake_checkout(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    make_skill(upstream / "skills", "new-skill")

    def checkout(spec, destination):
        shutil.copytree(upstream, destination, dirs_exist_ok=True)

    monkeypatch.setattr(
        intake,
        "preflight_source_tree",
        lambda spec: {"file_count": 1, "byte_count": 100},
    )
    monkeypatch.setattr(intake, "checkout_pinned_source", checkout)
    return upstream


@pytest.fixture
def prepared_paths(valid_root, tmp_path, fake_checkout):
    stage = tmp_path / "stage"
    intake.prepare_source(valid_root, valid_source_spec(), stage)
    review_path = stage / "review.json"
    review = json.loads(review_path.read_text())
    review["decisions"][0].update(
        {
            "decision": "import",
            "reason": "Fixture candidate reviewed",
            "taxonomy": "engineering/testing",
            "category_fine": "testing",
            "canonical_skill_id": None,
        }
    )
    write_json(review_path, review)
    return stage / "manifest.json", review_path


@pytest.fixture
def manifest(prepared_paths):
    return prepared_paths[0]


@pytest.fixture
def valid_review(prepared_paths):
    return json.loads(prepared_paths[1].read_text())


def apply_review_mutation(review, mutation):
    decisions = review["decisions"]
    if mutation == "pending_decision":
        decisions[0]["decision"] = "pending"
    elif mutation == "missing_candidate":
        decisions.pop()
    elif mutation == "extra_candidate":
        extra = copy.deepcopy(decisions[0])
        extra["source_path"] = "skills/extra"
        decisions.append(extra)
    elif mutation == "duplicate_candidate":
        decisions.append(copy.deepcopy(decisions[0]))
    elif mutation == "empty_reason":
        decisions[0]["reason"] = ""
    elif mutation == "invalid_taxonomy":
        decisions[0]["taxonomy"] = "../escape"
    elif mutation == "invalid_category":
        decisions[0]["category_fine"] = "Not A Slug"
    elif mutation == "canonical_without_target":
        decisions[0].update({"decision": "canonical", "canonical_skill_id": None})
    elif mutation == "unknown_canonical_target":
        decisions[0].update(
            {"decision": "canonical", "canonical_skill_id": "asr_ffffffffffffffff"}
        )
    elif mutation == "self_canonical_target":
        decisions[0].update(
            {
                "decision": "canonical",
                "canonical_skill_id": stable_skill_id(
                    "new-source", decisions[0]["source_path"]
                ),
            }
        )
    elif mutation == "noncanonical_with_target":
        decisions[0]["canonical_skill_id"] = "asr_existing"
    elif mutation == "manifest_digest_mismatch":
        review["manifest_sha256"] = "0" * 64
    else:
        raise AssertionError(mutation)


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:org/repo.git",
        "https://gitlab.com/org/repo.git",
        "https://github.com/org/repo",
        "https://github.com/org/repo.git?token=secret",
    ],
)
def test_validate_source_rejects_noncanonical_url(url):
    with pytest.raises(IntakeError):
        validate_source_spec(valid_source_spec(url=url))


def test_validate_source_requires_exact_commit():
    with pytest.raises(IntakeError):
        validate_source_spec(valid_source_spec(commit="main"))


def test_validate_source_requires_license_evidence():
    with pytest.raises(IntakeError):
        validate_source_spec(valid_source_spec(license=""))


@pytest.mark.parametrize("field", ["source_id", "url", "commit", "skills_root", "license", "license_note"])
def test_validate_source_requires_every_field(field):
    spec = valid_source_spec()
    del spec[field]
    with pytest.raises(IntakeError, match="fields"):
        validate_source_spec(spec)


def test_validate_source_rejects_unknown_fields():
    with pytest.raises(IntakeError, match="fields"):
        validate_source_spec(valid_source_spec(extra="value"))


def test_validate_source_returns_new_normalized_dictionary():
    spec = valid_source_spec(license=" MIT ", license_note=" Evidence ")
    normalized = validate_source_spec(spec)
    assert normalized == valid_source_spec(license_note="Evidence")
    assert list(normalized) == [
        "source_id",
        "url",
        "commit",
        "skills_root",
        "license",
        "license_note",
    ]
    assert normalized is not spec
    assert spec["license"] == " MIT "


@pytest.mark.parametrize("source_id", ["ab", "Upper", "bad_id", "-leading"])
def test_validate_source_rejects_invalid_source_id(source_id):
    with pytest.raises(IntakeError, match="source_id"):
        validate_source_spec(valid_source_spec(source_id=source_id))


@pytest.mark.parametrize(
    "skills_root",
    [
        "**",
        "skills/*",
        "skills/[ab]",
        "skills?",
        "skills//nested",
        ".",
        "../skills",
        "/skills",
        "skills/../other",
    ],
)
def test_validate_source_rejects_nonliteral_skills_root(skills_root):
    with pytest.raises(IntakeError, match="skills_root"):
        validate_source_spec(valid_source_spec(skills_root=skills_root))


def test_preflight_rejects_truncated_tree():
    body = io.BytesIO(json.dumps({"truncated": True, "tree": []}).encode())
    with pytest.raises(IntakeError, match="truncated"):
        preflight_source_tree(valid_source_spec(), opener=lambda request, timeout: body)


def test_preflight_rejects_source_byte_limit(monkeypatch):
    monkeypatch.setattr(intake, "MAX_SOURCE_BYTES", 10)
    tree = {
        "truncated": False,
        "tree": [{"type": "blob", "path": "skills/example/SKILL.md", "size": 11}],
    }
    with pytest.raises(IntakeError, match="source byte limit"):
        preflight_source_tree(
            valid_source_spec(),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


def test_preflight_rejects_source_file_limit(monkeypatch):
    monkeypatch.setattr(intake, "MAX_SOURCE_FILES", 1)
    tree = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "skills/a/SKILL.md", "size": 1},
            {"type": "blob", "path": "skills/b/SKILL.md", "size": 1},
        ],
    }
    with pytest.raises(IntakeError, match="source file limit"):
        preflight_source_tree(
            valid_source_spec(),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


def test_preflight_counts_only_cone_materialized_blobs_and_sets_request_contract():
    seen = {}
    tree = {
        "truncated": False,
        "tree": [
            {"type": "tree", "path": "skills", "size": 999},
            {"type": "blob", "path": "skills/a/SKILL.md", "size": 4},
            {"type": "blob", "path": "unrelated/outside.txt", "size": 20},
        ],
    }

    def opener(request, timeout):
        seen.update({"request": request, "timeout": timeout})
        return io.BytesIO(json.dumps(tree).encode())

    assert preflight_source_tree(valid_source_spec(), opener=opener) == {
        "file_count": 1,
        "byte_count": 4,
    }
    request = seen["request"]
    assert request.full_url == (
        "https://api.github.com/repos/example/skills/git/trees/"
        + "c" * 40
        + "?recursive=1"
    )
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("User-agent") == "agentic-skill-registry"
    assert seen["timeout"] == 30


def test_preflight_counts_oversized_top_level_blob_for_nested_skills_root(monkeypatch):
    monkeypatch.setattr(intake, "MAX_SOURCE_BYTES", 10)
    tree = {
        "truncated": False,
        "tree": [{"type": "blob", "path": "README.md", "size": 11}],
    }
    with pytest.raises(IntakeError, match="source byte limit"):
        preflight_source_tree(
            valid_source_spec(skills_root="platform/skills"),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


def test_preflight_counts_oversized_ancestor_blob_for_nested_skills_root(monkeypatch):
    monkeypatch.setattr(intake, "MAX_SOURCE_BYTES", 10)
    tree = {
        "truncated": False,
        "tree": [{"type": "blob", "path": "platform/NOTICE", "size": 11}],
    }
    with pytest.raises(IntakeError, match="source byte limit"):
        preflight_source_tree(
            valid_source_spec(skills_root="platform/skills"),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


@pytest.mark.parametrize(
    "tree",
    [
        {"truncated": False, "tree": {}},
        {"truncated": False, "tree": [{"type": "blob", "path": "skills/a"}]},
        {"truncated": False, "tree": [{"type": "blob", "path": "skills/a", "size": True}]},
    ],
)
def test_preflight_rejects_invalid_tree_payload(tree):
    with pytest.raises(IntakeError):
        preflight_source_tree(
            valid_source_spec(),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


def test_preflight_rejects_missing_blob_size_outside_skills_root():
    tree = {
        "truncated": False,
        "tree": [{"type": "blob", "path": "outside.txt"}],
    }
    with pytest.raises(IntakeError, match="size"):
        preflight_source_tree(
            valid_source_spec(),
            opener=lambda request, timeout: io.BytesIO(json.dumps(tree).encode()),
        )


def test_preflight_wraps_opener_and_json_errors():
    def fail(request, timeout):
        raise OSError("network down")

    with pytest.raises(IntakeError, match="preflight"):
        preflight_source_tree(valid_source_spec(), opener=fail)
    with pytest.raises(IntakeError, match="preflight"):
        preflight_source_tree(
            valid_source_spec(), opener=lambda request, timeout: io.BytesIO(b"not json")
        )


def test_checkout_disables_credentials_and_uses_exact_sparse_filter(tmp_path):
    calls = []

    def runner(command, **kwargs):
        assert kwargs["env"]["HOME"]
        assert not os.listdir(kwargs["env"]["HOME"])
        calls.append((command, kwargs))
        stdout = "c" * 40 + "\n" if command[-2:] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    destination = tmp_path / "checkout"
    checkout_pinned_source(valid_source_spec(), destination, runner=runner)
    commands = [command for command, _ in calls]
    assert commands == [
        ["git", "init", "--", str(destination)],
        ["git", "-C", str(destination), "remote", "add", "origin", valid_source_spec()["url"]],
        [
            "git",
            "-C",
            str(destination),
            "sparse-checkout",
            "set",
            "--cone",
            "--",
            "skills",
        ],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "credential.helper=",
            "fetch",
            "--depth",
            "1",
            "--filter=blob:none",
            "origin",
            "c" * 40,
        ],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "credential.helper=",
            "checkout",
            "--detach",
            "FETCH_HEAD",
        ],
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
    ]
    for _, kwargs in calls:
        assert kwargs["timeout"] == 60
        assert kwargs["check"] is True
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GIT_ASKPASS"] == "/usr/bin/false"
        assert kwargs["env"]["GCM_INTERACTIVE"] == "never"
        assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert kwargs["env"]["GIT_ATTR_NOSYSTEM"] == "1"
        assert kwargs["env"]["GIT_LFS_SKIP_SMUDGE"] == "1"
        assert set(kwargs["env"]) == {
            "PATH",
            "HOME",
            "GIT_TERMINAL_PROMPT",
            "GIT_ASKPASS",
            "GCM_INTERACTIVE",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_ATTR_NOSYSTEM",
            "GIT_LFS_SKIP_SMUDGE",
        }


@pytest.mark.parametrize("destination_kind", ["directory", "symlink"])
def test_checkout_rejects_preexisting_destination_before_runner(tmp_path, destination_kind):
    destination = tmp_path / "checkout"
    if destination_kind == "directory":
        destination.mkdir()
    else:
        destination.symlink_to(tmp_path / "missing", target_is_directory=True)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(IntakeError, match="destination"):
        checkout_pinned_source(valid_source_spec(), destination, runner=runner)
    assert calls == []


def test_checkout_requires_exact_commit(tmp_path):
    def runner(command, **kwargs):
        stdout = "d" * 40 + "\n" if command[-2:] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with pytest.raises(IntakeError, match="commit"):
        checkout_pinned_source(valid_source_spec(), tmp_path / "checkout", runner=runner)


def test_discovery_recurses_deterministically_and_skips_nested_markers(tmp_path):
    root = tmp_path / "skills"
    first = make_skill(root, "a")
    make_skill(first, "nested")
    second = make_skill(root / "group", "b")
    (root / "empty").mkdir(parents=True)
    assert discover_source_bundles(root) == [first, second]


def test_discovery_rejects_symlink_skills_root(tmp_path):
    target = tmp_path / "target"
    make_skill(target, "example")
    root = tmp_path / "skills"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(IntakeError, match="symlink"):
        discover_source_bundles(root)


def test_discovery_rejects_relative_symlink_escape(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    make_skill(outside, "example")
    (root / "escape").symlink_to("../outside", target_is_directory=True)
    with pytest.raises(IntakeError, match="symlink"):
        discover_source_bundles(root)


def test_parse_frontmatter_requires_valid_object_with_name_and_description(tmp_path):
    marker = tmp_path / "SKILL.md"
    marker.write_text("---\nname: valid\ndescription: Useful.\n---\n", encoding="utf-8")
    assert parse_skill_frontmatter(marker) == {"name": "valid", "description": "Useful."}


@pytest.mark.parametrize(
    "content",
    [
        "name: no-frontmatter\n",
        "---\n[not, an, object]\n---\n",
        "---\nname: [broken\n---\n",
        "---\nname: ''\ndescription: useful\n---\n",
        "---\nname: valid\ndescription: 3\n---\n",
    ],
)
def test_parse_frontmatter_rejects_invalid_metadata(tmp_path, content):
    marker = tmp_path / "SKILL.md"
    marker.write_text(content, encoding="utf-8")
    with pytest.raises(IntakeError, match="frontmatter"):
        parse_skill_frontmatter(marker)


def test_parse_frontmatter_rejects_invalid_utf8_as_intake_error(tmp_path):
    marker = tmp_path / "SKILL.md"
    marker.write_bytes(b"---\nname: example\ndescription: \xff\n---\n")
    with pytest.raises(IntakeError, match="frontmatter"):
        parse_skill_frontmatter(marker)


def test_inspect_bundle_rejects_missing_root_marker(tmp_path):
    bundle = tmp_path / "example"
    bundle.mkdir()
    with pytest.raises(IntakeError, match="SKILL.md"):
        inspect_bundle(bundle)


def test_inspect_bundle_rejects_symlink_bundle_root(tmp_path):
    target = make_skill(tmp_path, "target")
    bundle = tmp_path / "example"
    bundle.symlink_to(target, target_is_directory=True)
    with pytest.raises(IntakeError, match="symlink"):
        inspect_bundle(bundle)


def test_inspect_bundle_rejects_symlink(tmp_path):
    bundle = make_skill(tmp_path, "example")
    (bundle / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(IntakeError, match="symlink"):
        inspect_bundle(bundle)


def test_inspect_bundle_rejects_hardlink(tmp_path):
    bundle = make_skill(tmp_path, "example")
    os.link(bundle / "SKILL.md", bundle / "copy.md")
    with pytest.raises(IntakeError, match="hardlink"):
        inspect_bundle(bundle)


def test_inspect_bundle_rejects_file_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUNDLE_FILES", 1)
    bundle = make_skill(tmp_path, "example", extra_files={"a.txt": "a"})
    with pytest.raises(IntakeError, match="file limit"):
        inspect_bundle(bundle)


def test_inspect_bundle_rejects_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUNDLE_BYTES", 10)
    bundle = make_skill(tmp_path, "example")
    with pytest.raises(IntakeError, match="byte limit"):
        inspect_bundle(bundle)


def test_inspect_bundle_returns_metadata_counts_and_hash(tmp_path):
    bundle = make_skill(tmp_path, "example", extra_files={"notes.txt": "abc"})
    result = inspect_bundle(bundle)
    assert result["name"] == "example"
    assert result["description"] == "Use example."
    assert result["file_count"] == 2
    assert result["byte_count"] == sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())
    assert len(result["content_sha256"]) == 64


def test_classification_aggregates_existing_taxonomy_votes():
    value = {"name": "pytest-helper", "description": "Test Python code with pytest"}
    index = [
        discovery("unit-tests", "engineering/testing", "testing", "pytest unit testing"),
        discovery("test-review", "engineering/testing", "testing", "review automated tests"),
        discovery("pdf", "documents/pdf", "documents", "edit PDF files"),
    ]

    assert intake.propose_classification(value, index) == {
        "taxonomy": "engineering/testing",
        "category_fine": "testing",
        "classification_status": "proposed",
    }


def test_classification_falls_back_when_no_terms_match():
    assert intake.propose_classification(
        {"name": "xyzzy", "description": "plugh"}, []
    ) == {
        "taxonomy": "workflows-and-management/uncategorized-and-misc",
        "category_fine": "uncategorized",
        "classification_status": "proposed",
    }


def test_classification_breaks_score_ties_lexicographically():
    value = {"name": "python-helper", "description": "Review Python"}
    index = [
        discovery("python", "zeta/testing", "zeta", "review"),
        discovery("python", "alpha/testing", "alpha", "review"),
    ]

    assert intake.propose_classification(value, index)["taxonomy"] == "alpha/testing"


def test_duplicate_evidence_detects_exact_hash():
    evidence = intake.duplicate_evidence(
        candidate(content_sha256="a" * 64), [existing()], []
    )

    assert evidence == [
        {
            "kind": "exact_hash",
            "skill_id": "asr_existing",
            "action": "canonical_candidate",
        }
    ]


def test_duplicate_evidence_detects_same_source_path():
    evidence = intake.duplicate_evidence(
        candidate(source_id="existing-source", source_path="skills/existing"),
        [existing()],
        [],
    )

    assert evidence == [
        {
            "kind": "same_source_path",
            "skill_id": "asr_existing",
            "action": "update_review",
        }
    ]


def test_duplicate_evidence_detects_name_collision():
    evidence = intake.duplicate_evidence(
        candidate(load_name="existing-skill"), [existing()], []
    )

    assert evidence == [
        {
            "kind": "name_collision",
            "skill_id": "asr_existing",
            "action": "review",
        }
    ]


def test_duplicate_evidence_detects_normalized_name_collision():
    evidence = intake.duplicate_evidence(
        candidate(name="Existing Skill", load_name="new-skill"), [existing()], []
    )

    assert evidence == [
        {
            "kind": "name_collision",
            "skill_id": "asr_existing",
            "action": "review",
        }
    ]


def test_duplicate_evidence_marks_similarity_for_review_only():
    evidence = intake.duplicate_evidence(
        candidate(name="python-test-review", description="security"),
        [existing(load_name="python-test-audit")],
        [
            discovery(
                "python-test-audit",
                "engineering/testing",
                "testing",
                "python test review security",
                skill_id="asr_existing",
            )
        ],
    )

    assert evidence == [
        {
            "kind": "functional_similarity",
            "skill_id": "asr_existing",
            "score": 0.8,
            "action": "review",
        }
    ]
    assert not {"decision", "canonical_skill_id", "state", "risk"}.intersection(
        evidence[0]
    )


def test_duplicate_evidence_is_sorted_by_kind_and_skill_id():
    records = [
        existing(skill_id="asr_zeta", content_sha256="b" * 64),
        existing(skill_id="asr_alpha", content_sha256="b" * 64),
    ]

    evidence = intake.duplicate_evidence(candidate(), records, [])

    assert [(item["kind"], item["skill_id"]) for item in evidence] == [
        ("exact_hash", "asr_alpha"),
        ("exact_hash", "asr_zeta"),
    ]


def test_prepare_source_does_not_mutate_repository(valid_root, tmp_path, fake_checkout):
    before = repository_digest(valid_root)

    payload = intake.prepare_source(
        valid_root, valid_source_spec(), tmp_path / "stage"
    )

    assert repository_digest(valid_root) == before
    assert payload == json.loads((tmp_path / "stage/manifest.json").read_text())


def test_prepare_source_rejects_existing_source_id(valid_root, tmp_path, fake_checkout):
    spec = valid_source_spec(source_id="existing-source")

    with pytest.raises(IntakeError, match="source_id already exists"):
        intake.prepare_source(valid_root, spec, tmp_path / "stage")


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_prepare_source_rejects_and_preserves_existing_staging(
    kind, valid_root, tmp_path, fake_checkout
):
    staging = tmp_path / "stage"
    preserved = tmp_path / "preserved"
    preserved.mkdir()
    marker = preserved / "marker.txt"
    marker.write_text("keep\n", encoding="utf-8")
    if kind == "directory":
        staging.mkdir()
        (staging / "marker.txt").write_text("keep\n", encoding="utf-8")
        expected_marker = staging / "marker.txt"
    else:
        staging.symlink_to(preserved, target_is_directory=True)
        expected_marker = marker

    with pytest.raises(IntakeError, match="staging already exists"):
        intake.prepare_source(valid_root, valid_source_spec(), staging)

    assert staging.exists()
    assert expected_marker.read_text(encoding="utf-8") == "keep\n"


def test_prepare_source_rejects_staging_inside_repository_without_mutation(
    valid_root, fake_checkout
):
    staging = valid_root / "staging"
    before = repository_digest(valid_root)

    with pytest.raises(IntakeError, match="inside repository"):
        intake.prepare_source(valid_root, valid_source_spec(), staging)

    assert repository_digest(valid_root) == before
    assert not staging.exists()


def test_prepare_source_is_deterministic(valid_root, tmp_path, fake_checkout):
    first = intake.prepare_source(valid_root, valid_source_spec(), tmp_path / "a")
    second = intake.prepare_source(valid_root, valid_source_spec(), tmp_path / "b")

    assert first == second
    assert "prepared_at" not in first


def test_prepare_source_builds_expected_candidate(valid_root, tmp_path, fake_checkout):
    payload = intake.prepare_source(
        valid_root, valid_source_spec(), tmp_path / "stage"
    )

    assert payload["schema_version"] == 1
    assert payload["source"] == valid_source_spec()
    assert payload["candidates"] == [
        {
            "source_path": "skills/new-skill",
            "name": "new-skill",
            "description": "Use new-skill.",
            "content_sha256": payload["candidates"][0]["content_sha256"],
            "safety_profile": {
                "content_sha256": payload["candidates"][0]["content_sha256"],
                "scanner_version": 1,
                "status": "scanned",
                "signals": [],
                "severity": "clean",
                "evidence": [],
            },
            "file_count": 1,
            "byte_count": 65,
            "proposed_taxonomy": "workflows-and-management/uncategorized-and-misc",
            "proposed_category_fine": "uncategorized",
            "duplicate_evidence": [],
        }
    ]


def test_prepare_binds_review_to_exact_manifest(valid_root, tmp_path, fake_checkout):
    intake.prepare_source(valid_root, valid_source_spec(), tmp_path / "stage")
    manifest_bytes = (tmp_path / "stage/manifest.json").read_bytes()
    review = json.loads((tmp_path / "stage/review.json").read_text())

    assert review == {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "decisions": [
            {
                "source_path": "skills/new-skill",
                "decision": "pending",
                "taxonomy": "workflows-and-management/uncategorized-and-misc",
                "category_fine": "uncategorized",
                "canonical_skill_id": None,
                "reason": "",
            }
        ],
    }


def test_prepare_source_removes_staging_when_review_write_fails(
    valid_root, tmp_path, fake_checkout, monkeypatch
):
    staging = tmp_path / "stage"
    real_dump = intake.dump_json_atomic
    calls = 0

    def fail_second_write(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected review write failure")
        real_dump(path, value)

    monkeypatch.setattr(intake, "dump_json_atomic", fail_second_write)

    with pytest.raises(OSError, match="injected review write failure"):
        intake.prepare_source(valid_root, valid_source_spec(), staging)

    assert not staging.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "pending_decision",
        "missing_candidate",
        "extra_candidate",
        "duplicate_candidate",
        "empty_reason",
        "invalid_taxonomy",
        "invalid_category",
        "canonical_without_target",
        "unknown_canonical_target",
        "self_canonical_target",
        "noncanonical_with_target",
        "manifest_digest_mismatch",
    ],
)
def test_validate_review_rejects_incomplete_contract(
    mutation, manifest, valid_review
):
    apply_review_mutation(valid_review, mutation)

    with pytest.raises(IntakeError):
        intake.validate_review(
            manifest.read_bytes(), valid_review, known_skill_ids={"asr_existing"}
        )


def test_validate_review_accepts_complete_contract(manifest, valid_review):
    assert (
        intake.validate_review(
            manifest.read_bytes(), valid_review, known_skill_ids={"asr_existing"}
        )
        is None
    )


def test_source_review_artifact_records_every_decision(manifest, valid_review):
    payload = intake.source_review_artifact(
        json.loads(manifest.read_text())["source"],
        manifest.read_bytes(),
        json.loads(manifest.read_text())["candidates"],
        valid_review["decisions"],
    )

    assert payload["manifest_sha256"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    assert payload["candidate_count"] == len(payload["decisions"])
    assert all(item["content_sha256"] for item in payload["decisions"])


@pytest.mark.parametrize(
    ("upstream_name", "expected"),
    [
        ("New Skill", "new-skill"),
        ("new_skill.v2", "new-skill-v2"),
        ("--Already--Slugged--", "already-slugged"),
    ],
)
def test_slugify_load_name_normalizes_only_new_names(upstream_name, expected):
    assert intake.slugify_load_name(upstream_name) == expected


@pytest.mark.parametrize("upstream_name", ["!!!", "x" * 129])
def test_slugify_load_name_rejects_invalid_result(upstream_name):
    with pytest.raises(IntakeError, match="invalid load name"):
        intake.slugify_load_name(upstream_name)


def test_next_load_name_preserves_free_name_and_stably_disambiguates_collisions():
    assert intake.next_load_name("new-skill", "new-source", set()) == "new-skill"
    assert intake.next_load_name("new-skill", "new-source", {"new-skill"}) == (
        "new-source--new-skill"
    )
    assert intake.next_load_name(
        "new-skill",
        "new-source",
        {"new-skill", "new-source--new-skill", "new-source--new-skill--2"},
    ) == "new-source--new-skill--3"


@pytest.mark.parametrize(
    "taxonomy", ["../escape", "engineering/*", "engineering", "/absolute/x"]
)
def test_catalog_destination_rejects_unsafe_taxonomy(valid_root, taxonomy):
    with pytest.raises(IntakeError, match="catalog destination"):
        intake.catalog_destination(valid_root, taxonomy, "new-skill")


def test_catalog_destination_rejects_symlinked_parent(valid_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (valid_root / "catalog/engineering").symlink_to(outside, target_is_directory=True)

    with pytest.raises(IntakeError, match="symlink"):
        intake.catalog_destination(valid_root, "engineering/testing", "new-skill")


def test_catalog_destination_rejects_existing_target(valid_root):
    destination = valid_root / "catalog/engineering/testing/new-skill"
    destination.mkdir(parents=True)

    with pytest.raises(IntakeError, match="destination exists"):
        intake.catalog_destination(valid_root, "engineering/testing", "new-skill")


def test_catalog_destination_returns_only_validated_relative_and_absolute_paths(
    valid_root,
):
    relative, destination = intake.catalog_destination(
        valid_root, "engineering/testing", "new-skill"
    )

    assert relative == "catalog/engineering/testing/new-skill"
    assert destination == valid_root / relative


def test_commit_refetches_and_rejects_changed_hash(
    valid_root, manifest, prepared_paths, fake_checkout
):
    marker = fake_checkout / "skills/new-skill/SKILL.md"
    marker.write_text(marker.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    before = repository_digest(valid_root)

    with pytest.raises(IntakeError, match="changed since preparation"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before


def test_commit_rejects_manifest_that_omits_pinned_candidate(
    valid_root, tmp_path, fake_checkout
):
    make_skill(fake_checkout / "skills", "omitted-skill")
    stage = tmp_path / "complete-stage"
    intake.prepare_source(valid_root, valid_source_spec(), stage)
    manifest_path = stage / "manifest.json"
    review_path = stage / "review.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["candidates"] = [
        item
        for item in manifest["candidates"]
        if item["source_path"] != "skills/omitted-skill"
    ]
    write_json(manifest_path, manifest)
    review = json.loads(review_path.read_text())
    review["manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    review["decisions"] = [
        item
        for item in review["decisions"]
        if item["source_path"] != "skills/omitted-skill"
    ]
    review["decisions"][0].update(
        {
            "decision": "reject",
            "reason": "Fixture scope rejection",
        }
    )
    write_json(review_path, review)

    with pytest.raises(IntakeError, match="candidate set differs"):
        intake.commit_source(valid_root, manifest_path, review_path)


def test_commit_revalidates_rejected_candidate_hash(
    valid_root, manifest, prepared_paths, fake_checkout
):
    review = json.loads(prepared_paths[1].read_text())
    review["decisions"][0].update(
        {
            "decision": "reject",
            "reason": "Fixture scope rejection",
        }
    )
    write_json(prepared_paths[1], review)
    marker = fake_checkout / "skills/new-skill/SKILL.md"
    marker.write_text(marker.read_text() + "changed\n")

    with pytest.raises(IntakeError, match="changed since preparation"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])


def test_commit_persists_source_review_artifact(
    valid_root, manifest, prepared_paths
):
    intake.commit_source(valid_root, manifest, prepared_paths[1])

    source = json.loads(
        (valid_root / "registry/sources.lock.json").read_text()
    )["sources"][-1]
    artifact_path = valid_root / (
        "registry/source-reviews/"
        f"{source['source_id']}-{source['commit']}.json"
    )
    artifact = json.loads(artifact_path.read_text())

    assert source["review"] == {
        "status": "reviewed",
        "artifact": artifact_path.relative_to(valid_root).as_posix(),
        "manifest_sha256": artifact["manifest_sha256"],
    }
    assert artifact["candidate_count"] == 1
    assert artifact["decisions"][0]["content_sha256"]


def test_commit_removes_only_new_review_artifact_on_rollback(
    valid_root, manifest, prepared_paths, monkeypatch
):
    existing_artifact = valid_root / "registry/source-reviews/existing.json"
    existing_artifact.parent.mkdir()
    existing_artifact.write_text("preserve\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(valid_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(valid_root), "commit", "-qm", "existing artifact"],
        check=True,
    )
    monkeypatch.setattr(
        intake,
        "verify_repository",
        lambda root: type("Report", (), {"result": "fail", "findings": []})(),
    )

    with pytest.raises(IntakeError, match="post-commit strict verification"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    artifact_path = valid_root / (
        "registry/source-reviews/new-source-" + "c" * 40 + ".json"
    )
    assert not artifact_path.exists()
    assert existing_artifact.read_text(encoding="utf-8") == "preserve\n"


def test_commit_rejects_missing_reviewed_candidate(
    valid_root, manifest, prepared_paths, fake_checkout
):
    shutil.rmtree(fake_checkout / "skills/new-skill")
    before = repository_digest(valid_root)

    with pytest.raises(IntakeError, match="candidate set differs"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before


def test_commit_rejects_manifest_changed_after_review(
    valid_root, manifest, prepared_paths
):
    before = repository_digest(valid_root)
    manifest.write_bytes(manifest.read_bytes() + b"\n")

    with pytest.raises(IntakeError, match="manifest digest"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before


def test_commit_requires_clean_worktree(valid_root, manifest, prepared_paths):
    path = valid_root / "registry/schema-version.json"
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(IntakeError, match="clean worktree"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])


def test_commit_validates_current_json_before_mutation(
    valid_root, manifest, prepared_paths
):
    index_path = valid_root / "librarian-index.json"
    write_json(index_path, {"schemaVersion": 1, "entries": [None]})
    subprocess.run(["git", "-C", str(valid_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(valid_root), "commit", "-qm", "malformed index"],
        check=True,
    )
    before = repository_digest(valid_root)

    with pytest.raises(IntakeError, match="registry records are invalid"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before
    assert not (valid_root / "catalog/engineering").exists()


def test_commit_rejects_existing_catalog_destination(
    valid_root, manifest, prepared_paths
):
    collision = valid_root / "catalog/engineering/testing/new-skill"
    collision.mkdir(parents=True)
    (collision / "reserved.txt").write_text("reserved\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(valid_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(valid_root), "commit", "-qm", "collision"], check=True
    )
    before = repository_digest(valid_root)

    with pytest.raises(IntakeError, match="destination exists"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before


def test_commit_rolls_back_catalog_and_json_on_write_failure(
    valid_root, manifest, prepared_paths, monkeypatch
):
    sentinel = valid_root / "catalog/keep/sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep")
    before = repository_digest(valid_root)
    original_bytes = {
        path.relative_to(valid_root).as_posix(): path.read_bytes()
        for path in [
            valid_root / "registry/sources.lock.json",
            valid_root / "registry/skills.json",
            valid_root / "registry/quarantine.json",
            valid_root / "librarian-index.json",
        ]
    }
    real_write = intake.dump_json_atomic
    calls = 0

    def fail_on_second_write(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected transaction write failure")
        real_write(path, value)

    monkeypatch.setattr(intake, "dump_json_atomic", fail_on_second_write)
    monkeypatch.setattr(intake, "_require_clean_worktree", lambda root: None)

    with pytest.raises(OSError, match="injected transaction write failure"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before
    assert not (valid_root / "catalog/engineering").exists()
    assert sentinel.read_text() == "keep"
    assert {
        path: (valid_root / path).read_bytes() for path in original_bytes
    } == original_bytes


def test_commit_preflight_failure_preserves_json_metadata(
    valid_root, manifest, prepared_paths, monkeypatch
):
    paths = [
        valid_root / "registry/sources.lock.json",
        valid_root / "registry/skills.json",
        valid_root / "registry/quarantine.json",
        valid_root / "librarian-index.json",
    ]
    for path in paths:
        path.chmod(0o640)
    before = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in paths
    }

    def fail_preflight(*args, **kwargs):
        raise IntakeError("injected preflight failure")

    monkeypatch.setattr(intake, "preflight_source_tree", fail_preflight)

    with pytest.raises(IntakeError, match="injected preflight failure"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in paths
    } == before


def test_commit_first_json_write_failure_preserves_json_metadata(
    valid_root, manifest, prepared_paths, monkeypatch
):
    paths = [
        valid_root / "registry/sources.lock.json",
        valid_root / "registry/skills.json",
        valid_root / "registry/quarantine.json",
        valid_root / "librarian-index.json",
    ]
    before = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in paths
    }

    def fail_first_write(*args, **kwargs):
        raise OSError("injected first write failure")

    monkeypatch.setattr(intake, "dump_json_atomic", fail_first_write)

    with pytest.raises(OSError, match="injected first write failure"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in paths
    } == before


def test_commit_rolls_back_parent_created_before_later_mkdir_failure(
    valid_root, manifest, prepared_paths, monkeypatch
):
    before = repository_digest(valid_root)
    catalog_root = valid_root / "catalog"
    real_mkdir = Path.mkdir
    catalog_mkdir_calls = 0

    def fail_second_catalog_mkdir(path, *args, **kwargs):
        nonlocal catalog_mkdir_calls
        if path.is_relative_to(catalog_root) and not path.exists():
            catalog_mkdir_calls += 1
            if catalog_mkdir_calls == 2:
                raise OSError("injected second mkdir failure")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_second_catalog_mkdir)

    with pytest.raises(OSError, match="injected second mkdir failure"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert catalog_mkdir_calls == 2
    assert repository_digest(valid_root) == before
    assert not (valid_root / "catalog/engineering").exists()


def test_commit_rolls_back_when_post_commit_strict_verification_fails(
    valid_root, manifest, prepared_paths, monkeypatch
):
    before = repository_digest(valid_root)

    class FailedReport:
        result = "fail"
        findings = ({"check_id": "injected.strict.failure"},)

    monkeypatch.setattr(intake, "verify_repository", lambda root: FailedReport())

    with pytest.raises(IntakeError, match="post-commit strict verification failed"):
        intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert repository_digest(valid_root) == before
    assert not (valid_root / "catalog/engineering").exists()


def test_commit_imports_reviewed_snapshot_and_builds_joined_json_in_memory(
    valid_root, manifest, prepared_paths
):
    result = intake.commit_source(valid_root, manifest, prepared_paths[1])

    assert result == {
        "canonical": 0,
        "imported": 1,
        "quarantined": 0,
        "rejected": 0,
        "result": "pass",
        "strict_verifier": "pass",
    }

    lock = json.loads((valid_root / "registry/sources.lock.json").read_text())
    assert lock["sources"][-1] == {
        "source_id": "new-source",
        "url": "https://github.com/example/skills.git",
        "commit": "c" * 40,
        "layout": "skills-subdir",
        "skills_root": "skills",
        "metadata_index": None,
        "license_note": "Fixture source license",
        "status": "active",
        "refreshable": True,
        "timeout_seconds": 15,
        "review": {
            "status": "reviewed",
            "artifact": (
                "registry/source-reviews/new-source-" + "c" * 40 + ".json"
            ),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        },
    }
    record = json.loads((valid_root / "registry/skills.json").read_text())["skills"][0]
    assert record == {
        "skill_id": stable_skill_id("new-source", "skills/new-skill"),
        "name": "new-skill",
        "load_name": "new-skill",
        "catalog_path": "catalog/engineering/testing/new-skill",
        "source_id": "new-source",
        "source_commit": "c" * 40,
        "source_path": "skills/new-skill",
        "content_sha256": record["content_sha256"],
        "license": "MIT",
        "risk": "unknown",
        "risk_reasons": ["initial-review-required"],
        "state": "active",
        "canonical_skill_id": None,
        "first_seen_version": "0.2.0",
    }
    assert "url" not in record and "license_note" not in record
    index = json.loads((valid_root / "librarian-index.json").read_text())
    assert index["entries"] == [
        {
            "skill_id": record["skill_id"],
            "name": "new-skill",
            "flat_name": "new-skill",
            "taxonomy": "engineering/testing",
            "category_fine": "testing",
            "description": "Use new-skill.",
            "risk": "unknown",
            "license": "MIT",
            "canonical": None,
        }
    ]
    assert (valid_root / record["catalog_path"] / "SKILL.md").is_file()
    assert json.loads((valid_root / "registry/schema-version.json").read_text()) == {
        "schema_version": 1
    }


def test_commit_source_writes_profile_for_each_accepted_bundle(
    valid_root, manifest, prepared_paths
):
    intake.commit_source(valid_root, manifest, prepared_paths[1])

    record = json.loads((valid_root / "registry/skills.json").read_text())["skills"][0]
    profiles = json.loads(
        (valid_root / "registry/safety-signals.json").read_text()
    )["profiles"]

    assert profiles == [
        {
            "skill_id": record["skill_id"],
            "content_sha256": record["content_sha256"],
            "scanner_version": 1,
            "status": "scanned",
            "signals": [],
            "severity": "clean",
            "evidence": [],
        }
    ]


def test_generate_safety_signals_rebuilds_profiles_for_active_bundles(
    valid_root, manifest, prepared_paths
):
    intake.commit_source(valid_root, manifest, prepared_paths[1])
    target = valid_root / "registry/safety-signals.json"
    target.unlink()

    result = subprocess.run(
        [
            sys.executable,
            "tools/generate_safety_signals.py",
            "--root",
            str(valid_root),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    profile = json.loads(target.read_text())["profiles"][0]
    assert profile["content_sha256"] == json.loads(
        (valid_root / "registry/skills.json").read_text()
    )["skills"][0]["content_sha256"]


def test_commit_sets_discovery_index_count_when_input_omits_count(
    valid_root, manifest, prepared_paths
):
    index_path = valid_root / "librarian-index.json"
    index = json.loads(index_path.read_text())
    index.pop("count")
    write_json(index_path, index)
    subprocess.run(
        ["git", "-C", str(valid_root), "add", "librarian-index.json"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(valid_root), "commit", "-qm", "fixture without count"],
        check=True,
    )

    intake.commit_source(valid_root, manifest, prepared_paths[1])

    output = json.loads(index_path.read_text())
    assert output["count"] == len(output["entries"])


def test_commit_preserves_legacy_quarantine_records_without_load_names(
    valid_root, manifest, prepared_paths
):
    quarantine_path = valid_root / "registry/quarantine.json"
    payload = json.loads(quarantine_path.read_text())
    payload["records"] = [
        {
            "skill_id": stable_skill_id("existing-source", "legacy/one"),
            "source_id": "existing-source",
            "source_commit": "a" * 40,
            "source_path": "legacy/one",
            "catalog_path": None,
            "content_sha256": "1" * 64,
            "rule_ids": ["missing-skill-md"],
            "disposition": "quarantined",
            "name": "one",
        },
        {
            "skill_id": stable_skill_id("existing-source", "legacy/two"),
            "source_id": "existing-source",
            "source_commit": "a" * 40,
            "source_path": "legacy/two",
            "catalog_path": None,
            "content_sha256": "2" * 64,
            "rule_ids": ["missing-skill-md"],
            "disposition": "quarantined",
            "name": "two",
        },
    ]
    write_json(quarantine_path, payload)
    subprocess.run(["git", "-C", str(valid_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(valid_root), "commit", "-qm", "legacy quarantine"],
        check=True,
    )

    intake.commit_source(valid_root, manifest, prepared_paths[1])

    records = json.loads(quarantine_path.read_text())["records"]
    assert records[:2] == payload["records"]


@pytest.mark.parametrize("decision", ["quarantine", "reject"])
def test_commit_maps_nonactive_review_decisions(
    decision, valid_root, manifest, prepared_paths
):
    review_path = prepared_paths[1]
    review = json.loads(review_path.read_text())
    review["decisions"][0]["decision"] = decision
    write_json(review_path, review)

    intake.commit_source(valid_root, manifest, review_path)

    skills = json.loads((valid_root / "registry/skills.json").read_text())["skills"]
    quarantine = json.loads(
        (valid_root / "registry/quarantine.json").read_text()
    )["records"]
    if decision == "quarantine":
        assert skills == []
        assert len(quarantine) == 1
        assert quarantine[0]["state"] == "quarantined"
        assert quarantine[0]["risk"] == "unknown"
        assert quarantine[0]["disposition"] == "quarantined"
        assert quarantine[0]["rule_ids"] == ["initial-review-required"]
    else:
        assert skills == []
        assert quarantine == []
        assert not (valid_root / "catalog/engineering/testing/new-skill").exists()


def test_dump_json_atomic_removes_temporary_file_when_replace_fails(
    tmp_path, monkeypatch
):
    target = tmp_path / "payload.json"
    unrelated = tmp_path / ".payload.json.tmp"
    unrelated.write_text("preserve\n", encoding="utf-8")
    attempted_temporary = None

    def fail_replace(self, target):
        nonlocal attempted_temporary
        attempted_temporary = self
        raise OSError("injected replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        filesystem.dump_json_atomic(target, {"value": "example"})

    assert attempted_temporary is not None
    assert not attempted_temporary.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve\n"
    assert not target.exists()


def test_dump_json_atomic_uses_distinct_temporary_files_for_concurrent_writers(
    tmp_path, monkeypatch
):
    target = tmp_path / "payload.json"
    barrier = threading.Barrier(2)
    temporaries = []
    real_replace = Path.replace

    def synchronized_replace(self, target):
        temporaries.append(self)
        barrier.wait(timeout=2)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(filesystem.dump_json_atomic, target, {"writer": writer})
            for writer in (1, 2)
        ]
        for future in futures:
            future.result(timeout=3)

    assert len(set(temporaries)) == 2
    assert all(path.parent == tmp_path for path in temporaries)
    assert json.loads(target.read_text(encoding="utf-8")) in [
        {"writer": 1},
        {"writer": 2},
    ]


def test_dump_json_atomic_preserves_mapping_order(tmp_path):
    target = tmp_path / "payload.json"

    filesystem.dump_json_atomic(target, {"z_existing": 1, "a_new": 2})

    assert target.read_text(encoding="utf-8").index('"z_existing"') < target.read_text(
        encoding="utf-8"
    ).index('"a_new"')
