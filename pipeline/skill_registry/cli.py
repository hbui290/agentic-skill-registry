import argparse
import json
import sys
from pathlib import Path

from skill_registry.intake import (
    IntakeError,
    commit_source,
    commit_source_update,
    prepare_source,
    prepare_source_update,
)
from skill_registry.refresh import SourceRefreshError, refresh_sources
from skill_registry.runtime import (
    RegistryRuntimeError,
    SkillBlocked,
    read_skill,
    search_skills,
)
from skill_registry.validator import verify_repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-registry")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--strict", action="store_true")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    verify.add_argument("--format", choices=("text", "json"), default="text")
    verify.add_argument("--output", type=Path)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--root", type=Path, default=Path.cwd())
    refresh.add_argument("--format", choices=("text", "json"), default="text")
    refresh.add_argument("--output", type=Path)
    search = commands.add_parser("search")
    search.add_argument("query", nargs="+")
    search.add_argument("--root", type=Path, default=Path.cwd())
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--format", choices=("text", "json"), default="text")
    read = commands.add_parser("read")
    read.add_argument("identifier")
    read.add_argument("--root", type=Path, default=Path.cwd())
    read.add_argument("--format", choices=("text", "json"), default="text")
    prepare = commands.add_parser("prepare-source")
    prepare.add_argument("--root", type=Path, default=Path.cwd())
    prepare.add_argument("--source-id", required=True)
    prepare.add_argument("--url", required=True)
    prepare.add_argument("--commit", required=True)
    prepare.add_argument("--skills-root", required=True)
    prepare.add_argument("--license", required=True)
    prepare.add_argument("--license-note", required=True)
    prepare.add_argument("--staging", type=Path, required=True)
    prepare.add_argument("--format", choices=("text", "json"), default="text")
    commit = commands.add_parser("commit-source")
    commit.add_argument("--root", type=Path, default=Path.cwd())
    commit.add_argument("--manifest", type=Path, required=True)
    commit.add_argument("--review", type=Path, required=True)
    commit.add_argument("--format", choices=("text", "json"), default="text")
    prepare_update = commands.add_parser("prepare-update")
    prepare_update.add_argument("--root", type=Path, default=Path.cwd())
    prepare_update.add_argument("--source-id", required=True)
    prepare_update.add_argument("--url", required=True)
    prepare_update.add_argument("--commit", required=True)
    prepare_update.add_argument("--skills-root", required=True)
    prepare_update.add_argument("--license", required=True)
    prepare_update.add_argument("--license-note", required=True)
    prepare_update.add_argument("--staging", type=Path, required=True)
    prepare_update.add_argument(
        "--format", choices=("text", "json"), default="text"
    )
    commit_update = commands.add_parser("commit-update")
    commit_update.add_argument("--root", type=Path, default=Path.cwd())
    commit_update.add_argument("--manifest", type=Path, required=True)
    commit_update.add_argument("--review", type=Path, required=True)
    commit_update.add_argument(
        "--format", choices=("text", "json"), default="text"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare-update":
        spec = {
            "source_id": args.source_id,
            "url": args.url,
            "commit": args.commit,
            "skills_root": args.skills_root,
            "license": args.license,
            "license_note": args.license_note,
        }
        try:
            manifest = prepare_source_update(
                args.root.resolve(), spec, args.staging
            )
        except (IntakeError, OSError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        candidates = manifest["candidates"]
        payload = {
            "added": sum(
                candidate["change"] == "added" for candidate in candidates
            ),
            "modified": sum(
                candidate["change"] == "modified" for candidate in candidates
            ),
            "path_corrected": len(manifest["path_corrections"]),
            "result": "prepared",
            "review_required_count": len(candidates),
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"added={payload['added']} modified={payload['modified']} "
                f"path_corrected={payload['path_corrected']} "
                f"review_required={payload['review_required_count']}"
            )
        return 0
    if args.command == "commit-update":
        try:
            payload = commit_source_update(
                args.root.resolve(), args.manifest, args.review
            )
        except (IntakeError, OSError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"added={payload['added']} modified={payload['modified']} "
                f"quarantined={payload['quarantined']} "
                f"path_corrected={payload['path_corrected']} "
                f"strict_verifier={payload['strict_verifier']}"
            )
        return 0
    if args.command == "prepare-source":
        spec = {
            "source_id": args.source_id,
            "url": args.url,
            "commit": args.commit,
            "skills_root": args.skills_root,
            "license": args.license,
            "license_note": args.license_note,
        }
        try:
            manifest = prepare_source(args.root.resolve(), spec, args.staging)
        except (IntakeError, OSError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        candidate_count = len(manifest["candidates"])
        payload = {
            "candidate_count": candidate_count,
            "result": "prepared",
            "review_required_count": candidate_count,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"candidates={candidate_count} "
                f"review_required={candidate_count}"
            )
        return 0
    if args.command == "commit-source":
        try:
            payload = commit_source(args.root.resolve(), args.manifest, args.review)
        except (IntakeError, OSError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"imported={payload['imported']} "
                f"canonical={payload['canonical']} "
                f"quarantined={payload['quarantined']} "
                f"rejected={payload['rejected']} "
                f"strict_verifier={payload['strict_verifier']}"
            )
        return 0
    if args.command == "search":
        try:
            payload = search_skills(args.root.resolve(), " ".join(args.query), args.limit)
        except (RegistryRuntimeError, ValueError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif not payload["matches"]:
            print("no matches")
        else:
            for candidate in payload["matches"]:
                print(
                    f"{candidate['load_name']} | {candidate['risk']} | "
                    f"{candidate['taxonomy']} | {candidate['description']}"
                )
        return 0
    if args.command == "read":
        try:
            payload = read_skill(args.root.resolve(), args.identifier)
        except (SkillBlocked, RegistryRuntimeError) as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(payload["instructions"], end="")
        return 0
    if args.command == "refresh":
        try:
            payload = refresh_sources(args.root.resolve())
        except SourceRefreshError as error:
            print(f"error={error}", file=sys.stderr)
            return 1
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        elif args.format == "json":
            print(rendered, end="")
        else:
            for source in payload["sources"]:
                print(f"source={source['source_id']} status={source['status']}")
        return 1 if payload["result"] == "error" else 0
    report = verify_repository(args.root.resolve())
    payload = report.to_dict()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    elif args.format == "json":
        print(rendered, end="")
    else:
        print(f"result={report.result} failed={report.failed}")
    return 1 if report.failed or (args.strict and (report.warnings or report.skipped)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
