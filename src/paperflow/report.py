from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from paperflow import __version__
from paperflow.models import RankedPaper, SourceFailure


def _generated_at(now: datetime | None) -> str:
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include timezone information")
    return value.isoformat()


def _yaml_scalar(value: str) -> str:
    escaped = []
    for character in value:
        if character == "\\":
            escaped.append("\\\\")
        elif character == '"':
            escaped.append('\\"')
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif ord(character) < 32:
            escaped.append(f"\\u{ord(character):04x}")
        else:
            escaped.append(character)
    return f'"{"".join(escaped)}"'


def _excerpt(abstract: str) -> str:
    return abstract if len(abstract) <= 500 else f"{abstract[:500]}…"


def _stable_sources(papers: list[RankedPaper]) -> list[str]:
    seen: set[str] = set()
    sources = []
    for ranked in papers:
        for source in ranked.paper.sources:
            if source not in seen:
                seen.add(source)
                sources.append(source)
    return sources


def _single_line(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def render_daily_markdown(
    report_date: str,
    papers: list[RankedPaper],
    failures: list[SourceFailure],
    *,
    now: datetime | None = None,
) -> str:
    sources = _stable_sources(papers)
    lines = [
        "---",
        f"date: {_yaml_scalar(report_date)}",
        f"generated_at: {_yaml_scalar(_generated_at(now))}",
        f"paperflow_version: {_yaml_scalar(__version__)}",
        f"partial: {'true' if failures else 'false'}",
    ]
    if sources:
        lines.append("sources:")
        lines.extend(f"  - {_yaml_scalar(source)}" for source in sources)
    else:
        lines.append("sources: []")
    lines.extend(["---", "", f"# PaperFlow Daily Report — {report_date}", ""])

    if failures:
        lines.extend(["## Source failures", ""])
        for failure in failures:
            lines.extend(
                [
                    f"- source: {_single_line(failure.source)}",
                    f"  message: {_single_line(failure.message)}",
                ]
            )
        lines.append("")

    for index, ranked in enumerate(papers, start=1):
        paper = ranked.paper
        source_values = ", ".join(f"`{_single_line(value)}`" for value in paper.sources)
        matched_values = ", ".join(
            f"`{_single_line(value)}`" for value in ranked.matched_keywords
        )
        lines.extend(
            [
                f"## {index}. {_single_line(paper.title)}",
                "",
                f"- arxiv_id: `{_single_line(paper.arxiv_id)}`",
                f"- title: {_single_line(paper.title)}",
                "- authors: "
                + ", ".join(_single_line(value) for value in paper.authors),
                f"- sources: {source_values}",
                f"- score: {ranked.score}",
                f"- matched: {matched_values}",
                f"- arXiv: [Abstract]({_single_line(paper.url)})",
                f"- PDF: [PDF]({_single_line(paper.pdf_url)})",
                f"- abstract: {_single_line(_excerpt(paper.abstract))}",
                "",
            ]
        )
    return "\n".join(lines)


def render_email_html(
    report_date: str,
    papers: list[RankedPaper],
    failures: list[SourceFailure],
) -> str:
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<body>",
        f"<h1>PaperFlow Daily Report — {escape(report_date)}</h1>",
        f"<p>Partial: {'yes' if failures else 'no'}</p>",
    ]
    if failures:
        lines.extend(["<h2>Source failures</h2>", "<ul>"])
        lines.extend(
            f"<li><strong>{escape(failure.source)}</strong>: "
            f"{escape(failure.message)}</li>"
            for failure in failures
        )
        lines.append("</ul>")

    for index, ranked in enumerate(papers, start=1):
        paper = ranked.paper
        authors = ", ".join(escape(value) for value in paper.authors)
        sources = ", ".join(escape(value) for value in paper.sources)
        matched = ", ".join(escape(value) for value in ranked.matched_keywords)
        lines.extend(
            [
                f"<h2>{index}. {escape(paper.title)}</h2>",
                "<ul>",
                f"<li>arXiv ID: {escape(paper.arxiv_id)}</li>",
                f"<li>Authors: {authors}</li>",
                f"<li>Sources: {sources}</li>",
                f"<li>Score: {ranked.score}</li>",
                f"<li>Matched: {matched}</li>",
                f'<li><a href="{escape(paper.url, quote=True)}">arXiv</a></li>',
                f'<li><a href="{escape(paper.pdf_url, quote=True)}">PDF</a></li>',
                "</ul>",
                f"<p>Abstract: {escape(_excerpt(paper.abstract))}</p>",
            ]
        )
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines)


def render_email_text(
    report_date: str,
    papers: list[RankedPaper],
    failures: list[SourceFailure],
) -> str:
    lines = [
        f"PaperFlow Daily Report — {report_date}",
        f"Partial: {'yes' if failures else 'no'}",
        "",
    ]
    if failures:
        lines.append("Source failures:")
        lines.extend(
            f"- {_single_line(failure.source)}: {_single_line(failure.message)}"
            for failure in failures
        )
        lines.append("")

    for index, ranked in enumerate(papers, start=1):
        paper = ranked.paper
        lines.extend(
            [
                f"{index}. {_single_line(paper.title)}",
                f"Authors: {', '.join(_single_line(value) for value in paper.authors)}",
                f"Sources: {', '.join(_single_line(value) for value in paper.sources)}",
                f"Score: {ranked.score}",
                "Matched: "
                + ", ".join(
                    _single_line(value) for value in ranked.matched_keywords
                ),
                f"arXiv: {_single_line(paper.url)}",
                f"PDF: {_single_line(paper.pdf_url)}",
                f"Abstract: {_single_line(_excerpt(paper.abstract))}",
                "",
            ]
        )
    return "\n".join(lines)
