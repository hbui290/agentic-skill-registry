import argparse
import json
import sys
from pathlib import Path

from skill_registry.refresh import SourceRefreshError, refresh_sources
from skill_registry.runtime import (
    RegistryRuntimeError,
    SkillBlocked,
    SkillConfirmationRequired,
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
    read.add_argument("--allow-unreviewed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
            payload = read_skill(
                args.root.resolve(), args.identifier, args.allow_unreviewed
            )
        except SkillConfirmationRequired as error:
            print(f"confirmation_required={error}", file=sys.stderr)
            return 3
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
            print(f"error={error}")
            return 1
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        elif args.format == "json":
            print(rendered, end="")
        else:
            for source in payload["sources"]:
                print(f"source={source['source_id']} status={source['status']}")
        return 0
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
