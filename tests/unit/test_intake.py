import io
import json
import os
import subprocess

import pytest

from skill_registry import intake
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
