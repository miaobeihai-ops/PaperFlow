from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

from paperflow import __version__
from paperflow.models import RankedPaper, SourceFailure


_MARKDOWN_ESCAPES = frozenset(r"\\`*_[]<>()!|~{}&")


def _generated_at(now: datetime | None) -> str:
    value = now if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include timezone information")
    return value.isoformat()


def _yaml_scalar(value: str) -> str:
    escaped = []
    for character in value:
        codepoint = ord(character)
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
        elif (
            codepoint < 32
            or 0x7F <= codepoint <= 0x9F
            or codepoint in (0x2028, 0x2029)
        ):
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(character)
    return f'"{"".join(escaped)}"'


def _excerpt(abstract: str) -> str:
    return abstract if len(abstract) <= 500 else f"{abstract[:500]}…"


def _visible_control(character: str) -> str | None:
    codepoint = ord(character)
    if character == "\n":
        return r"\n"
    if character == "\r":
        return r"\r"
    if character == "\t":
        return r"\t"
    if (
        codepoint < 32
        or 0x7F <= codepoint <= 0x9F
        or codepoint in (0x2028, 0x2029)
    ):
        return f"\\u{codepoint:04x}"
    return None


def escape_markdown_text(value: str) -> str:
    rendered = []
    for character in value:
        visible = _visible_control(character)
        if visible is not None:
            rendered.append(visible)
        elif character in _MARKDOWN_ESCAPES:
            rendered.append(f"\\{character}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _code_span(value: str) -> str:
    rendered = "".join(_visible_control(character) or character for character in value)
    longest_run = 0
    current_run = 0
    for character in rendered:
        if character == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    fence = "`" * (longest_run + 1)
    needs_padding = rendered.startswith("`") or rendered.endswith("`")
    needs_padding = needs_padding or (
        rendered.strip(" ") != ""
        and (rendered.startswith(" ") or rendered.endswith(" "))
    )
    padding = " " if needs_padding else ""
    return f"{fence}{padding}{rendered}{padding}{fence}"


def _link_destination(value: str) -> str:
    rendered = []
    for character in value:
        if character in "() <>\\" or _visible_control(character) is not None:
            rendered.append(quote(character, safe=""))
        else:
            rendered.append(character)
    return "".join(rendered)


def _stable_sources(papers: list[RankedPaper]) -> list[str]:
    seen: set[str] = set()
    sources = []
    for ranked in papers:
        for source in ranked.paper.sources:
            if source not in seen:
                seen.add(source)
                sources.append(source)
    return sources


def _text_single_line(value: str) -> str:
    return "".join(_visible_control(character) or character for character in value)


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
                    f"- source: {escape_markdown_text(failure.source)}",
                    f"  message: {escape_markdown_text(failure.message)}",
                ]
            )
        lines.append("")

    for index, ranked in enumerate(papers, start=1):
        paper = ranked.paper
        source_values = ", ".join(_code_span(value) for value in paper.sources)
        matched_values = ", ".join(
            _code_span(value) for value in ranked.matched_keywords
        )
        lines.extend(
            [
                f"## {index}. {escape_markdown_text(paper.title)}",
                "",
                f"- arxiv_id: {_code_span(paper.arxiv_id)}",
                f"- title: {escape_markdown_text(paper.title)}",
                "- authors: "
                + ", ".join(escape_markdown_text(value) for value in paper.authors),
                f"- sources: {source_values}",
                f"- score: {ranked.score}",
                f"- matched: {matched_values}",
                f"- arXiv: [Abstract]({_link_destination(paper.url)})",
                f"- PDF: [PDF]({_link_destination(paper.pdf_url)})",
                f"- abstract: {escape_markdown_text(_excerpt(paper.abstract))}",
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
            f"- {_text_single_line(failure.source)}: "
            f"{_text_single_line(failure.message)}"
            for failure in failures
        )
        lines.append("")

    for index, ranked in enumerate(papers, start=1):
        paper = ranked.paper
        lines.extend(
            [
                f"{index}. {_text_single_line(paper.title)}",
                f"arXiv ID: {_text_single_line(paper.arxiv_id)}",
                "Authors: "
                + ", ".join(_text_single_line(value) for value in paper.authors),
                "Sources: "
                + ", ".join(_text_single_line(value) for value in paper.sources),
                f"Score: {ranked.score}",
                "Matched: "
                + ", ".join(
                    _text_single_line(value) for value in ranked.matched_keywords
                ),
                f"arXiv: {_text_single_line(paper.url)}",
                f"PDF: {_text_single_line(paper.pdf_url)}",
                f"Abstract: {_text_single_line(_excerpt(paper.abstract))}",
                "",
            ]
        )
    return "\n".join(lines)
