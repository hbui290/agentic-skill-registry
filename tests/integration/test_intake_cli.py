import hashlib
import json
from pathlib import Path

from skill_registry import cli
from skill_registry.intake import IntakeError


SECRET = "secret-token-should-not-be-rendered"
INSTRUCTIONS = "# Full upstream skill instructions must stay staged"


def repository_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_args(root: Path, staging: Path, output_format: str = "json") -> list[str]:
    return [
        "prepare-source",
        "--root",
        str(root),
        "--source-id",
        "microsoftdocs-agent-skills",
        "--url",
        "https://github.com/MicrosoftDocs/Agent-Skills.git",
        "--commit",
        "a" * 40,
        "--skills-root",
        "skills",
        "--license",
        "CC-BY-4.0",
        "--license-note",
        SECRET,
        "--staging",
        str(staging),
        "--format",
        output_format,
    ]


def test_prepare_source_json_is_read_only_and_does_not_render_staged_content(
    monkeypatch, capsys, tmp_path
):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "marker.txt").write_text("unchanged\n", encoding="utf-8")
    staging = tmp_path / "staging"
    before = repository_digest(root)

    def prepared(received_root, spec, received_staging):
        assert received_root == root.resolve()
        assert spec == {
            "source_id": "microsoftdocs-agent-skills",
            "url": "https://github.com/MicrosoftDocs/Agent-Skills.git",
            "commit": "a" * 40,
            "skills_root": "skills",
            "license": "CC-BY-4.0",
            "license_note": SECRET,
        }
        assert received_staging == staging
        return {
            "candidates": [
                {"description": SECRET, "instructions": INSTRUCTIONS},
                {"description": "safe summary"},
            ]
        }

    monkeypatch.setattr(cli, "prepare_source", prepared, raising=False)

    assert cli.main(prepare_args(root, staging)) == 0
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "candidate_count": 2,
        "result": "prepared",
        "review_required_count": 2,
    }
    assert captured.err == ""
    assert repository_digest(root) == before
    assert SECRET not in captured.out
    assert INSTRUCTIONS not in captured.out


def test_prepare_source_text_reports_only_counts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        cli,
        "prepare_source",
        lambda root, spec, staging: {
            "candidates": [{"instructions": INSTRUCTIONS}]
        },
        raising=False,
    )

    assert cli.main(prepare_args(tmp_path, tmp_path.parent / "staging", "text")) == 0
    captured = capsys.readouterr()

    assert captured.out == "candidates=1 review_required=1\n"
    assert captured.err == ""
    assert INSTRUCTIONS not in captured.out


def test_prepare_source_intake_error_uses_stderr_and_exit_one(
    monkeypatch, capsys, tmp_path
):
    def invalid_source(root, spec, staging):
        raise IntakeError("source URL is invalid")

    monkeypatch.setattr(cli, "prepare_source", invalid_source, raising=False)

    assert cli.main(prepare_args(tmp_path, tmp_path.parent / "staging")) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "error=source URL is invalid\n"


def test_commit_source_pending_review_exits_one_without_mutation(
    monkeypatch, capsys, tmp_path
):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "marker.txt").write_text("unchanged\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    review = tmp_path / "review.json"
    manifest.write_text("{}\n", encoding="utf-8")
    review.write_text(
        json.dumps({"decisions": [{"decision": "pending", "reason": SECRET}]}),
        encoding="utf-8",
    )
    before = repository_digest(root)

    def pending(received_root, received_manifest, received_review):
        assert received_root == root.resolve()
        assert received_manifest == manifest
        assert received_review == review
        raise IntakeError("review decision is invalid")

    monkeypatch.setattr(cli, "commit_source", pending, raising=False)

    result = cli.main(
        [
            "commit-source",
            "--root",
            str(root),
            "--manifest",
            str(manifest),
            "--review",
            str(review),
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == "error=review decision is invalid\n"
    assert repository_digest(root) == before
    assert SECRET not in captured.err


def test_commit_source_json_reports_decision_counts_without_review_content(
    monkeypatch, capsys, tmp_path
):
    manifest = tmp_path / "manifest.json"
    review = tmp_path / "review.json"
    manifest.write_text("{}\n", encoding="utf-8")
    review.write_text(
        json.dumps(
            {
                "decisions": [
                    {"decision": "import", "reason": SECRET},
                    {"decision": "import", "reason": SECRET},
                    {"decision": "canonical", "reason": SECRET},
                    {"decision": "quarantine", "reason": SECRET},
                    {"decision": "reject", "reason": SECRET},
                ]
            }
        ),
        encoding="utf-8",
    )
    def commit(root, manifest, review):
        review.unlink()
        return {
            "canonical": 1,
            "imported": 2,
            "quarantined": 1,
            "rejected": 1,
            "result": "pass",
            "strict_verifier": "pass",
        }

    monkeypatch.setattr(cli, "commit_source", commit, raising=False)

    result = cli.main(
        [
            "commit-source",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--review",
            str(review),
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert json.loads(captured.out) == {
        "canonical": 1,
        "imported": 2,
        "quarantined": 1,
        "rejected": 1,
        "result": "pass",
        "strict_verifier": "pass",
    }
    assert captured.err == ""
    assert SECRET not in captured.out


def test_commit_source_text_reports_counts_and_strict_verifier(
    monkeypatch, capsys, tmp_path
):
    manifest = tmp_path / "manifest.json"
    review = tmp_path / "review.json"
    manifest.write_text("{}\n", encoding="utf-8")
    review.write_text(
        json.dumps(
            {
                "decisions": [
                    {"decision": "canonical", "reason": INSTRUCTIONS},
                    {"decision": "reject", "reason": INSTRUCTIONS},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "commit_source",
        lambda root, manifest, review: {
            "canonical": 1,
            "imported": 0,
            "quarantined": 0,
            "rejected": 1,
            "result": "pass",
            "strict_verifier": "pass",
        },
        raising=False,
    )

    result = cli.main(
        [
            "commit-source",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--review",
            str(review),
            "--format",
            "text",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out == (
        "imported=0 canonical=1 quarantined=0 rejected=1 strict_verifier=pass\n"
    )
    assert captured.err == ""
    assert INSTRUCTIONS not in captured.out


def test_prepare_source_update_json_reports_delta_counts(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(
        cli,
        "prepare_source_update",
        lambda root, spec, staging: {
            "path_corrections": [{"from": "old", "to": "new"}],
            "candidates": [
                {"change": "path-corrected"},
                {"change": "modified"},
                {"change": "added"},
            ],
        },
        raising=False,
    )

    result = cli.main(
        [
            "prepare-update",
            "--root",
            str(tmp_path),
            "--source-id",
            "existing-source",
            "--url",
            "https://github.com/example/existing.git",
            "--commit",
            "c" * 40,
            "--skills-root",
            "skills",
            "--license",
            "per-skill",
            "--license-note",
            "reviewed",
            "--staging",
            str(tmp_path.parent / "stage"),
            "--format",
            "json",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "added": 1,
        "modified": 1,
        "path_corrected": 1,
        "result": "prepared",
        "review_required_count": 3,
    }


def test_commit_source_update_json_reports_result(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "manifest.json"
    review = tmp_path / "review.json"
    manifest.write_text("{}\n", encoding="utf-8")
    review.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "commit_source_update",
        lambda root, manifest, review: {
            "added": 39,
            "modified": 20,
            "path_corrected": 100,
            "result": "pass",
            "strict_verifier": "pass",
        },
        raising=False,
    )

    result = cli.main(
        [
            "commit-update",
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--review",
            str(review),
            "--format",
            "json",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["strict_verifier"] == "pass"


def test_commit_source_os_error_is_reported_without_traceback(monkeypatch, capsys, tmp_path):
    def fail(*_args):
        raise OSError("fixture read failure")

    monkeypatch.setattr(cli, "commit_source", fail)

    result = cli.main(
        [
            "commit-source",
            "--root",
            str(tmp_path),
            "--manifest",
            str(tmp_path / "missing-manifest.json"),
            "--review",
            str(tmp_path / "missing-review.json"),
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == "error=fixture read failure\n"
