from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from paperflow import __version__
from paperflow.config import (
    ConfigError,
    PaperFlowConfig,
    load_cloud_config,
    load_local_config,
)
from paperflow.daily import AllSourcesFailed, run_daily
from paperflow.models import DailyResult, SourceFailure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperflow")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    daily = subparsers.add_parser("daily")
    daily.add_argument("--date")
    daily.add_argument("--no-write", action="store_true")
    return parser


def _load_config() -> PaperFlowConfig:
    private_config = os.environ.get("PAPERFLOW_PRIVATE_CONFIG_JSON")
    if private_config is not None:
        return load_cloud_config(private_config)
    try:
        return load_local_config()
    except FileNotFoundError as exc:
        raise ConfigError("local configuration file was not found") from exc
    except OSError as exc:
        raise ConfigError("local configuration file could not be read") from exc


def _target_date(config: PaperFlowConfig, requested: str | None) -> str:
    if requested is not None:
        return requested
    try:
        timezone = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("configured timezone is invalid") from exc
    return datetime.now(timezone).date().isoformat()


def _failure_json(failure: SourceFailure) -> dict[str, str]:
    return {"source": failure.source, "message": failure.message}


def _result_json(result: DailyResult) -> dict[str, object]:
    return {
        "ok": True,
        "date": result.date,
        "partial": bool(result.failures),
        "papers": [
            {
                "arxiv_id": item.paper.arxiv_id,
                "title": item.paper.title,
                "score": item.score,
                "matched_keywords": list(item.matched_keywords),
                "sources": list(item.paper.sources),
                "url": item.paper.url,
                "pdf_url": item.paper.pdf_url,
            }
            for item in result.papers
        ],
        "failures": [_failure_json(failure) for failure in result.failures],
        "report_path": str(result.report_path) if result.report_path else None,
    }


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _print_result(result: DailyResult) -> None:
    suffix = " (partial)" if result.failures else ""
    print(f"{result.date}: {len(result.papers)} papers{suffix}")
    if result.report_path is not None:
        print(f"report: {result.report_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        if args.json_output:
            _print_json({"ok": True, "version": __version__})
        else:
            print(f"paperflow {__version__}")
        return 0
    if args.command is None:
        parser.print_help()
        return 0

    try:
        config = _load_config()
        target_date = _target_date(config, args.date)
        result = run_daily(
            config,
            target_date,
            write_report=not args.no_write,
        )
    except ConfigError as exc:
        if args.json_output:
            _print_json({"ok": False, "error": str(exc)})
        else:
            print(f"error: {exc}")
        return 2
    except AllSourcesFailed as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "failures": [_failure_json(failure) for failure in exc.failures],
        }
        if args.json_output:
            _print_json(payload)
        else:
            print(f"error: {exc}")
            for failure in exc.failures:
                print(f"{failure.source}: {failure.message}")
        return 3

    if args.json_output:
        _print_json(_result_json(result))
    else:
        _print_result(result)
    return 0


def console_main() -> None:
    raise SystemExit(main())
