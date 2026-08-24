from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from paperflow import __version__
from paperflow.arxiv_source import (
    ArxivPaperNotFound,
    ArxivResponseError,
    fetch_arxiv_by_id,
    search_arxiv,
)
from paperflow.config import (
    ConfigError,
    PaperFlowConfig,
    config_from_topics,
    load_cloud_config,
    load_local_config,
    require_paperflow_home,
)
from paperflow.daily import AllSourcesFailed, run_daily
from paperflow.doctor import run_checks
from paperflow.email import EmailDeliveryError, GmailSettings, send_daily_email
from paperflow.models import DailyResult, SourceFailure
from paperflow.normalize import canonical_arxiv_id
from paperflow.notes import NoteExists, paper_note_path, write_paper_note
from paperflow.report import render_email_html, render_email_text
from paperflow.search import search_history
from paperflow.topics import (
    TopicSettings,
    add_topic,
    load_topic_settings,
    remove_topic,
    resolve_topics_path,
)
from paperflow.providers import PROVIDERS
from paperflow.research_context import inspect_context, prepare_research
from paperflow.research_report import finalize_research


_PUBLIC_SOURCES = frozenset(("hf-daily", "hf-trending", "arxiv"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperflow")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    daily = subparsers.add_parser("daily")
    daily.add_argument("--date")
    daily.add_argument("--email", action="store_true")
    daily.add_argument("--no-write", action="store_true")
    daily.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--history-only", action="store_true")
    search.add_argument("--category", action="append", default=[])
    search.add_argument("--since")
    search.add_argument("--limit", default="20")
    search.add_argument("--sort", default="relevance")
    search.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    note = subparsers.add_parser("note")
    note.add_argument("arxiv_id")
    note.add_argument("--force", action="store_true")
    note.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument(
        "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
    )
    watch = subparsers.add_parser("watch")
    watch_commands = watch.add_subparsers(dest="watch_command", required=True)
    watch_list = watch_commands.add_parser("list")
    watch_add = watch_commands.add_parser("add")
    watch_add.add_argument("topic")
    watch_add.add_argument("--weight", required=True)
    watch_remove = watch_commands.add_parser("remove")
    watch_remove.add_argument("topic")
    for command in (watch_list, watch_add, watch_remove):
        command.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            default=argparse.SUPPRESS,
        )
    research = subparsers.add_parser("research")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    research_prepare = research_commands.add_parser("prepare")
    research_prepare.add_argument("--domain", required=True)
    research_inspect = research_commands.add_parser("inspect")
    research_inspect.add_argument("--domain", required=True)
    research_inspect.add_argument("--context", required=True)
    research_finalize = research_commands.add_parser("finalize")
    research_finalize.add_argument("--context", required=True)
    research_finalize.add_argument("--analysis", required=True)
    for command in (research_prepare, research_inspect, research_finalize):
        command.add_argument(
            "--json", action="store_true", dest="json_output", default=argparse.SUPPRESS
        )
    return parser


def _load_config() -> PaperFlowConfig:
    topics_path = resolve_topics_path(os.environ)
    private_config = os.environ.get("PAPERFLOW_PRIVATE_CONFIG_JSON")
    if private_config is not None:
        if topics_path is not None:
            return load_cloud_config(private_config, topics_path=topics_path)
        return load_cloud_config(private_config)
    try:
        if topics_path is not None:
            return load_local_config(topics_path=topics_path)
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


def _load_email_config() -> tuple[PaperFlowConfig, GmailSettings]:
    address = os.environ.get("PAPERFLOW_GMAIL_ADDRESS")
    app_password = os.environ.get("PAPERFLOW_GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise ConfigError("email configuration is incomplete")

    topics_path = resolve_topics_path(os.environ)
    private_config = os.environ.get("PAPERFLOW_PRIVATE_CONFIG_JSON")
    if private_config == "":
        raise ConfigError("email configuration is incomplete")
    if private_config is not None:
        if topics_path is not None:
            config = load_cloud_config(private_config, topics_path=topics_path)
        else:
            config = load_cloud_config(private_config)
    else:
        mail_to = os.environ.get("PAPERFLOW_MAIL_TO")
        if not mail_to or topics_path is None:
            raise ConfigError("email configuration is incomplete")
        config = config_from_topics(load_topic_settings(topics_path), mail_to=mail_to)

    if not config.mail_to:
        raise ConfigError("email configuration is incomplete")
    try:
        settings = GmailSettings(address, app_password, config.mail_to)
    except ValueError as exc:
        raise ConfigError("email configuration is invalid") from exc
    return config, settings


def _failure_json(failure: SourceFailure) -> dict[str, str]:
    return {"source": failure.source, "message": failure.message}


def _public_failures(
    failures: Sequence[SourceFailure],
) -> tuple[SourceFailure, ...]:
    public = []
    for failure in failures:
        source = (
            failure.source
            if isinstance(failure.source, str)
            and failure.source in _PUBLIC_SOURCES
            else "unknown"
        )
        message = failure.message
        message_is_public = isinstance(message, str) and message in (
            "request timed out",
            "network error",
        )
        if isinstance(message, str) and len(message) == 8 and message.startswith("HTTP "):
            status_text = message[5:]
            if all("0" <= character <= "9" for character in status_text):
                message_is_public = 100 <= int(status_text) <= 599
        public.append(
            SourceFailure(
                source,
                message if message_is_public else "source request failed",
            )
        )
    return tuple(public)


def _result_json(result: DailyResult) -> dict[str, object]:
    public_failures = _public_failures(result.failures)
    return {
        "ok": True,
        "date": result.date,
        "partial": bool(public_failures),
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
        "failures": [_failure_json(failure) for failure in public_failures],
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
    settings = None
    target_date = None
    try:
        if args.email:
            config, settings = _load_email_config()
        else:
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
        public_failures = _public_failures(exc.failures)
        failure_email_sent = False
        if settings is not None and target_date is not None:
            try:
                send_daily_email(
                    settings,
                    f"PaperFlow {target_date}",
                    render_email_text(target_date, [], public_failures),
                    render_email_html(target_date, [], public_failures),
                )
                failure_email_sent = True
            except EmailDeliveryError:
                pass
        payload = {
            "ok": False,
            "error": str(exc),
            "failures": [_failure_json(failure) for failure in public_failures],
        }
        if args.email:
            payload["failure_email_sent"] = failure_email_sent
        if args.json_output:
            _print_json(payload)
        else:
            print(f"error: {exc}")
            for failure in public_failures:
                print(f"{failure.source}: {failure.message}")
            if args.email:
                print(
                    "failure email: sent"
                    if failure_email_sent
                    else "failure email: failed"
                )
        return 3

    public_failures = _public_failures(result.failures)
    if settings is not None:
        try:
            send_daily_email(
                settings,
                f"PaperFlow {result.date}",
                render_email_text(result.date, result.papers, public_failures),
                render_email_html(result.date, result.papers, public_failures),
            )
        except EmailDeliveryError:
            payload = {
                "ok": False,
                "error": "email delivery failed",
                "email_sent": False,
            }
            if args.json_output:
                _print_json(payload)
            else:
                print("error: email delivery failed")
            return 5

    if args.json_output:
        payload = _result_json(result)
        if args.email:
            payload["email_sent"] = True
        _print_json(payload)
    else:
        _print_result(result)
        if args.email:
            print("email: sent")
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


def _parse_since(value: str | None, today: date | None = None) -> date | None:
    if value is None:
        return None
    reference = today or date.today()
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
        if days < 1:
            raise ConfigError("since duration must be positive")
        return reference - timedelta(days=days)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("since must be YYYY-MM-DD or Nd") from exc


def _parse_bounded_integer(
    value: str, name: str, lower: int, upper: int
) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lower <= parsed <= upper:
        raise ConfigError(f"{name} must be between {lower} and {upper}")
    return parsed


def _run_search(args: argparse.Namespace) -> int:
    try:
        limit = _parse_bounded_integer(args.limit, "limit", 1, 100)
        if args.sort not in ("relevance", "newest"):
            raise ConfigError("sort must be relevance or newest")
        since = _parse_since(args.since)
        categories = tuple(args.category)
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
                try:
                    online = search_arxiv(
                        client,
                        args.query,
                        max_results=limit,
                        categories=categories,
                        since=since,
                        sort=args.sort,
                    )
                except ArxivResponseError:
                    raise
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2
    except ArxivResponseError as exc:
        _print_error(args, str(exc))
        return 3
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
        "filters": {
            "categories": list(categories),
            "since": since.isoformat() if since else None,
            "limit": limit,
            "sort": args.sort,
        },
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


def _topic_payload(settings: TopicSettings) -> dict[str, object]:
    return {
        "topics": dict(sorted(settings.topics.items())),
        "arxiv_categories": list(settings.arxiv_categories),
        "timezone": settings.timezone,
        "top_n": settings.top_n,
        "history_reports": settings.history_reports,
    }


def _run_watch(args: argparse.Namespace) -> int:
    try:
        path = resolve_topics_path(os.environ)
        if path is None:
            raise ConfigError("topic file is not configured")
        before = load_topic_settings(path)
        action = "listed"
        changed = False
        topic = None
        weight = None
        settings = before
        if args.watch_command == "add":
            topic = args.topic.strip().casefold()
            existed = topic in before.topics
            weight = _parse_bounded_integer(args.weight, "weight", 1, 100)
            changed, settings = add_topic(path, topic, weight)
            action = (
                "updated"
                if existed and changed
                else "added"
                if changed
                else "unchanged"
            )
        elif args.watch_command == "remove":
            topic = args.topic.strip().casefold()
            changed, settings = remove_topic(path, topic)
            action = "removed" if changed else "unchanged"
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2

    payload = {
        "ok": True,
        "action": action,
        "changed": changed,
        "topic": topic,
        "weight": weight,
        "topics_path": str(path),
        **_topic_payload(settings),
    }
    if args.json_output:
        _print_json(payload)
    else:
        print(f"watch: {action}; topics: {len(settings.topics)}")
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
        if not args.force and paper_note_path(config.vault_path, arxiv_id).exists():
            raise NoteExists("paper note already exists")
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
    except ArxivResponseError as exc:
        _print_error(args, str(exc))
        return 3
    except ArxivPaperNotFound as exc:
        _print_error(args, str(exc))
        return 3
    except httpx.HTTPError:
        _print_error(args, "arXiv request failed")
        return 3
    except ValueError:
        _print_error(args, "paper note could not be created")
        return 3
    except OSError:
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


def _run_doctor(args: argparse.Namespace) -> int:
    checks = run_checks()
    ok = all(check.ok or not check.required for check in checks)
    if args.json_output:
        _print_json(
            {
                "ok": ok,
                "checks": [
                    {
                        "name": check.name,
                        "ok": check.ok,
                        "required": check.required,
                        "message": check.message,
                    }
                    for check in checks
                ],
            }
        )
    else:
        for check in checks:
            status = "OK" if check.ok else "FAIL" if check.required else "WARN"
            importance = "required" if check.required else "optional"
            print(f"[{status}] {check.name} ({importance}): {check.message}")
    return 0 if ok else 1


def _research_project_root() -> Path:
    configured = os.environ.get("PAPERFLOW_PROJECT_ROOT")
    if configured is None:
        return Path(__file__).resolve().parents[2]
    project_root = Path(configured)
    if not project_root.is_absolute() or not project_root.is_dir():
        raise ConfigError("PAPERFLOW_PROJECT_ROOT must be an absolute directory")
    return project_root


def _run_research(args: argparse.Namespace) -> int:
    try:
        home = require_paperflow_home(os.environ)
        if args.research_command == "prepare":
            result = prepare_research(
                args.domain,
                home,
                datetime.now(UTC),
                PROVIDERS,
                _research_project_root(),
            )
            payload = {
                "ok": True,
                "command": "research.prepare",
                "domain": result.domain,
                "run_id": result.run_id,
                "local_date": result.local_date,
                "context_path": str(result.context_path),
                "candidate_count": result.candidate_count,
                "partial": result.partial,
            }
        elif args.research_command == "inspect":
            summary = inspect_context(Path(args.context), home, args.domain)
            payload = {"ok": True, "command": "research.inspect", **summary}
        else:
            result = finalize_research(
                Path(args.context), Path(args.analysis), home=home
            )
            payload = {
                "ok": True,
                "command": "research.finalize",
                "domain": result.domain,
                "local_date": result.local_date,
                "selected_count": result.selected_count,
                "markdown_path": str(result.markdown_path),
                "json_path": str(result.json_path),
            }
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError):
        _print_error(args, "research file could not be read or written")
        return 3
    if args.json_output:
        _print_json(payload)
    else:
        print(f"{payload['command']}: {payload.get('domain', '')}")
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
    if args.command == "doctor":
        return _run_doctor(args)
    if args.command == "watch":
        return _run_watch(args)
    if args.command == "research":
        return _run_research(args)
    return 0


def console_main() -> None:
    raise SystemExit(main())
