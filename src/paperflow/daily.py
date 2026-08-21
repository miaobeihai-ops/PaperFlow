from __future__ import annotations

from datetime import date
import re

import httpx

from paperflow.arxiv_source import fetch_arxiv
from paperflow.config import ConfigError, PaperFlowConfig
from paperflow.hf_source import fetch_hf_daily, fetch_hf_trending
from paperflow.models import DailyResult, Paper, SourceFailure
from paperflow.normalize import deduplicate
from paperflow.obsidian import recent_arxiv_ids, write_daily_report
from paperflow.rank import rank_papers
from paperflow.report import render_daily_markdown


_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_GENERATED_AT_LINE = re.compile(r"^generated_at: .+$", re.MULTILINE)


class AllSourcesFailed(RuntimeError):
    def __init__(self, failures: tuple[SourceFailure, ...]) -> None:
        super().__init__("all paper sources failed")
        self.failures = failures


def _parse_date(value: str) -> date:
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise ConfigError("date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("date must use YYYY-MM-DD") from exc


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.NetworkError):
        return "network error"
    return type(exc).__name__


def _reuse_idempotent_report_content(
    config: PaperFlowConfig,
    target_date: str,
    content: str,
) -> str:
    if config.vault_path is None:
        return content
    target = config.vault_path / "PaperFlow" / "Reports" / f"{target_date}.md"
    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return content
    if _GENERATED_AT_LINE.sub("generated_at:", existing) == _GENERATED_AT_LINE.sub(
        "generated_at:", content
    ):
        return existing
    return content


def run_daily(
    config: PaperFlowConfig,
    target_date: str,
    *,
    write_report: bool = True,
) -> DailyResult:
    parsed_date = _parse_date(target_date)
    if write_report and config.vault_path is None:
        raise ConfigError("vault_path is required when writing a daily report")
    papers: list[Paper] = []
    failures: list[SourceFailure] = []
    successful_sources = 0

    with httpx.Client() as client:
        sources = (
            ("hf-daily", lambda: fetch_hf_daily(client, parsed_date)),
            ("hf-trending", lambda: fetch_hf_trending(client, parsed_date)),
            (
                "arxiv",
                lambda: fetch_arxiv(client, parsed_date, config.arxiv_categories),
            ),
        )
        for source, fetch in sources:
            try:
                papers.extend(fetch())
                successful_sources += 1
            except Exception as exc:
                failures.append(SourceFailure(source, _failure_message(exc)))

    if successful_sources == 0:
        raise AllSourcesFailed(tuple(failures))

    unique_papers = deduplicate(papers)
    if write_report:
        history = recent_arxiv_ids(
            config.vault_path,
            limit=config.history_reports,
            exclude_date=target_date,
        )
        unique_papers = [
            paper for paper in unique_papers if paper.arxiv_id not in history
        ]
    ranked = rank_papers(
        unique_papers,
        keywords=config.keywords,
        categories=config.arxiv_categories,
    )[: config.top_n]
    report_path = None
    if write_report:
        content = render_daily_markdown(target_date, ranked, failures)
        content = _reuse_idempotent_report_content(config, target_date, content)
        report_path = write_daily_report(config.vault_path, target_date, content)
    return DailyResult(target_date, tuple(ranked), tuple(failures), report_path)
