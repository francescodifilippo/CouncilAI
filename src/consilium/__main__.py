"""Command-line entry point.

    consilium run config/debate.example.yaml
    consilium roles
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .debate import Debate


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if not isinstance(config, dict) or "participants" not in config:
        raise SystemExit(f"{path}: not a debate configuration")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consilium",
        description="Turn-based debate orchestrator for AI CLIs and humans (Phase 0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a debate from a configuration file")
    run.add_argument("config", type=Path)
    run.add_argument(
        "--topic",
        help="override the topic in the configuration",
    )
    run.add_argument(
        "--max-rounds",
        type=int,
        help="override the round cap; the cap is the one mandatory automatic stop",
    )
    run.add_argument(
        "--root",
        type=Path,
        help="base for relative paths in the config (default: working directory)",
    )

    sub.add_parser("roles", help="list the role prompts available in roles/")

    args = parser.parse_args(argv)

    if args.command == "roles":
        roles_dir = Path.cwd() / "roles"
        if not roles_dir.is_dir():
            print("no roles/ directory here", file=sys.stderr)
            return 1
        for path in sorted(roles_dir.glob("*.txt")):
            first = path.read_text(encoding="utf-8").strip().splitlines()[0]
            print(f"  {path.stem:<20} {first}")
        return 0

    config_path = args.config.resolve()
    config = _load(config_path)
    if args.topic:
        config["topic"] = args.topic
    if args.max_rounds is not None:
        config.setdefault("cap", {})["max_rounds"] = args.max_rounds

    # Relative paths inside the configuration (role_prompt_file, transcript_dir)
    # resolve against --root, which defaults to the working directory. Run from
    # the repository root and `roles/sceptic.txt` just works.
    root = (args.root or Path.cwd()).resolve()

    outcome = Debate(config, root=root).run()
    return 0 if outcome in {"FINISHED", "STOPPED_BY_CAP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
