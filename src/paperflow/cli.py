from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from paperflow import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperflow")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        if args.json_output:
            print(json.dumps({"ok": True, "version": __version__}))
        else:
            print(f"paperflow {__version__}")
        return 0
    build_parser().print_help()
    return 0


def console_main() -> None:
    raise SystemExit(main())
