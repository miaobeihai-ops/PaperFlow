from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from paperflow import __version__
from paperflow.arxiv_source import (
    ArxivPaperNotFound,
    fetch_arxiv_by_id,
    search_arxiv,
)
from paperflow.config import (
    ConfigError,
    PaperFlowConfig,
    load_cloud_config,
    load_local_config,
)
from paperflow.daily import AllSourcesFailed, run_daily
from paperflow.models import DailyResult, SourceFailure
from paperflow.normalize import canonical_arxiv_id
from paperflow.notes import NoteExists, write_paper_note
from paperflow.search import search_history


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperflow")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    daily = subparsers.add_parser("daily")
    daily.add_argument("--date")
    daily.add_argument("--no-write", action="store_true")
    daily.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--history-only", action="store_true")
    search.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    note = subparsers.add_parser("note")
    note.add_argument("arxiv_id")
    note.add_argument("--force", action="store_true")
    note.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
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


def _print_error(args: argparse.Namespace, message: str) -> None:
    if args.json_output:
        _print_json({"ok": False, "error": message})
    else:
        print(f"error: {message}")


def _run_daily(args: argparse.Namespace) -> int:
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


def _paper_json(paper) -> dict[str, object]:
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": list(paper.authors),
        "abstract": paper.abstract,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
    }


def _run_search(args: argparse.Namespace) -> int:
    try:
        if not args.query.strip():
            raise ConfigError("query must not be blank")
        config = _load_config()
        if args.history_only and config.vault_path is None:
            raise ConfigError("vault_path is required for history-only search")
        history = (
            search_history(config.vault_path, args.query)
            if config.vault_path is not None
            else []
        )
        online = []
        if not args.history_only:
            with httpx.Client() as client:
                online = search_arxiv(client, args.query)
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2
    except (httpx.HTTPError, ArxivPaperNotFound):
        _print_error(args, "arXiv request failed")
        return 3
    except ValueError:
        _print_error(args, "arXiv response was invalid")
        return 3

    payload = {
        "ok": True,
        "query": args.query,
        "history": history,
        "online": [_paper_json(paper) for paper in online],
    }
    if args.json_output:
        _print_json(payload)
    else:
        print(f"history: {len(history)}; online: {len(online)}")
        for item in history:
            print(f"{item['arxiv_id']}  {item['title']}")
        for paper in online:
            print(f"{paper.arxiv_id}  {paper.title}")
    return 0


def _run_note(args: argparse.Namespace) -> int:
    try:
        config = _load_config()
        if config.vault_path is None:
            raise ConfigError("vault_path is required for paper notes")
        try:
            arxiv_id = canonical_arxiv_id(args.arxiv_id)
        except ValueError as exc:
            raise ConfigError("invalid arXiv identifier") from exc
        with httpx.Client() as client:
            paper = fetch_arxiv_by_id(client, arxiv_id)
        note_path = write_paper_note(
            config.vault_path,
            paper,
            force=args.force,
        )
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2
    except NoteExists as exc:
        _print_error(args, str(exc))
        return 4
    except ArxivPaperNotFound as exc:
        _print_error(args, str(exc))
        return 3
    except httpx.HTTPError:
        _print_error(args, "arXiv request failed")
        return 3
    except (ValueError, OSError):
        _print_error(args, "paper note could not be created")
        return 3

    payload = {
        "ok": True,
        "arxiv_id": arxiv_id,
        "note_path": str(note_path),
    }
    if args.json_output:
        _print_json(payload)
    else:
        print(f"note: {note_path}")
    return 0


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
    if args.command == "daily":
        return _run_daily(args)
    if args.command == "search":
        return _run_search(args)
    if args.command == "note":
        return _run_note(args)
    return 0


def console_main() -> None:
    raise SystemExit(main())
