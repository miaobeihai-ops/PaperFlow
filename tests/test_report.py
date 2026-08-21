from datetime import datetime, timezone
from pathlib import Path

import pytest

from paperflow.models import Paper, RankedPaper, SourceFailure
from paperflow.obsidian import recent_arxiv_ids, write_daily_report
from paperflow.report import (
    render_daily_markdown,
    render_email_html,
    render_email_text,
)


NOW = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)


def ranked_paper(
    arxiv_id: str = "2608.12345",
    *,
    title: str = "Robots < Vision",
    authors: tuple[str, ...] = ("Ada Researcher",),
    abstract: str = "A" * 600,
    sources: tuple[str, ...] = ("arxiv", "hf-daily"),
    url: str | None = None,
    pdf_url: str | None = None,
    matched: tuple[str, ...] = ("robotics",),
) -> RankedPaper:
    paper = Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors,
        abstract=abstract,
        primary_category="cs.RO",
        published="2026-08-20",
        sources=sources,
        hf_upvotes=12,
        url=url or f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
    )
    return RankedPaper(paper=paper, score=85, matched_keywords=matched)


def frontmatter(markdown: str) -> str:
    lines = markdown.splitlines()
    assert lines[0] == "---"
    closing = lines.index("---", 1)
    return "\n" + "\n".join(lines[1:closing]) + "\n"


def test_render_daily_markdown_has_stable_frontmatter_and_paper_fields():
    markdown = render_daily_markdown(
        "2026-08-20", [ranked_paper()], [], now=NOW
    )

    yaml = frontmatter(markdown)
    assert yaml == (
        '\ndate: "2026-08-20"\n'
        'generated_at: "2026-08-20T09:30:00+00:00"\n'
        'paperflow_version: "0.1.0"\n'
        "partial: false\n"
        "sources:\n"
        '  - "arxiv"\n'
        '  - "hf-daily"\n'
    )
    expected_fields = [
        "## 1. Robots < Vision",
        "- arxiv_id: `2608.12345`",
        "- title: Robots < Vision",
        "- authors: Ada Researcher",
        "- sources: `arxiv`, `hf-daily`",
        "- score: 85",
        "- matched: `robotics`",
        "- arXiv: [Abstract](https://arxiv.org/abs/2608.12345)",
        "- PDF: [PDF](https://arxiv.org/pdf/2608.12345)",
        f"- abstract: {'A' * 500}…",
    ]
    positions = [markdown.index(field) for field in expected_fields]
    assert positions == sorted(positions)


def test_render_daily_markdown_preserves_input_order_and_short_abstract():
    papers = [
        ranked_paper("2608.00002", title="Second", abstract="short"),
        ranked_paper("2608.00001", title="First", abstract="B" * 500),
    ]

    markdown = render_daily_markdown("2026-08-20", papers, [], now=NOW)

    assert markdown.index("## 1. Second") < markdown.index("## 2. First")
    assert "- abstract: short\n" in markdown
    assert f"- abstract: {'B' * 500}\n" in markdown
    assert f"{'B' * 500}…" not in markdown


def test_render_daily_markdown_marks_partial_and_lists_failures_before_papers():
    failure = SourceFailure("hf-trending", "timeout")

    markdown = render_daily_markdown(
        "2026-08-20", [ranked_paper()], [failure], now=NOW
    )

    assert "partial: true" in frontmatter(markdown)
    assert markdown.index("## Source failures") < markdown.index("## 1.")
    assert "- source: hf-trending" in markdown
    assert "  message: timeout" in markdown


def test_render_daily_markdown_deduplicates_successful_sources_stably():
    papers = [
        ranked_paper(sources=("hf-daily", "arxiv", "hf-daily")),
        ranked_paper("2608.00001", sources=("arxiv", "hf-trending")),
    ]

    yaml = frontmatter(render_daily_markdown("2026-08-20", papers, [], now=NOW))

    assert yaml.endswith(
        'sources:\n  - "hf-daily"\n  - "arxiv"\n  - "hf-trending"\n'
    )


def test_render_daily_markdown_external_source_cannot_close_frontmatter():
    malicious_source = 'arxiv"\n---\nowned: true'
    paper = ranked_paper(sources=(malicious_source,))

    markdown = render_daily_markdown("2026-08-20", [paper], [], now=NOW)

    assert markdown.splitlines().count("---") == 2
    yaml = frontmatter(markdown)
    assert '\\n---\\nowned: true' in yaml
    assert "\n---\nowned: true" not in yaml


def test_render_email_html_escapes_every_external_value_and_preserves_order():
    malicious = ranked_paper(
        title="Robots < Vision",
        authors=('Ada & <Researcher>',),
        abstract='<script>alert("abstract")</script>',
        sources=('arxiv&<source>',),
        matched=('robotics&<keyword>',),
        url='https://example.test/a?x=1&y="bad"',
        pdf_url='https://example.test/p?x=1&y="bad"',
    )
    second = ranked_paper("2608.00001", title="Later")
    failures = [SourceFailure('hf-trending<&', 'timeout <& "message"')]

    rendered = render_email_html("2026-08-20", [malicious, second], failures)

    assert "Robots &lt; Vision" in rendered
    assert "Ada &amp; &lt;Researcher&gt;" in rendered
    assert "&lt;script&gt;alert(&quot;abstract&quot;)&lt;/script&gt;" in rendered
    assert "arxiv&amp;&lt;source&gt;" in rendered
    assert "robotics&amp;&lt;keyword&gt;" in rendered
    assert "hf-trending&lt;&amp;" in rendered
    assert "timeout &lt;&amp; &quot;message&quot;" in rendered
    assert 'href="https://example.test/a?x=1&amp;y=&quot;bad&quot;"' in rendered
    assert 'href="https://example.test/p?x=1&amp;y=&quot;bad&quot;"' in rendered
    assert "<script>" not in rendered
    assert rendered.index("Robots &lt; Vision") < rendered.index("Later")


def test_render_email_text_contains_same_information_in_plain_text():
    rendered = render_email_text(
        "2026-08-20",
        [ranked_paper(abstract="short")],
        [SourceFailure("hf-trending", "timeout")],
    )

    expected = [
        "PaperFlow Daily Report — 2026-08-20",
        "Partial: yes",
        "Source failures:",
        "- hf-trending: timeout",
        "1. Robots < Vision",
        "Authors: Ada Researcher",
        "Sources: arxiv, hf-daily",
        "Score: 85",
        "Matched: robotics",
        "arXiv: https://arxiv.org/abs/2608.12345",
        "PDF: https://arxiv.org/pdf/2608.12345",
        "Abstract: short",
    ]
    positions = [rendered.index(value) for value in expected]
    assert positions == sorted(positions)
    assert "<html" not in rendered.lower()


def test_write_daily_report_creates_lf_utf8_target_and_replaces_same_day(tmp_path):
    target = write_daily_report(tmp_path, "2026-08-20", "first\r\nreport\r")
    replaced = write_daily_report(tmp_path, "2026-08-20", "café\r\nsecond")

    assert target == tmp_path / "PaperFlow" / "Reports" / "2026-08-20.md"
    assert replaced == target
    assert target.read_bytes() == "café\nsecond".encode("utf-8")
    assert list(target.parent.glob("*.tmp")) == []
    assert [path.name for path in target.parent.glob("2026-08-20*.md")] == [
        "2026-08-20.md"
    ]


def test_write_daily_report_cleans_temp_and_preserves_target_on_replace_failure(
    tmp_path, monkeypatch
):
    target = tmp_path / "PaperFlow" / "Reports" / "2026-08-20.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("paperflow.obsidian.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_daily_report(tmp_path, "2026-08-20", "replacement")

    assert target.read_text(encoding="utf-8") == "existing"
    assert list(target.parent.glob("*.tmp")) == []


def test_write_daily_report_cleans_partial_temp_and_preserves_target_on_write_failure(
    tmp_path, monkeypatch
):
    target = tmp_path / "PaperFlow" / "Reports" / "2026-08-20.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing", encoding="utf-8")
    original_open = Path.open

    class FailingWriter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            with original_open(self_path, "wb") as stream:
                stream.write(value[:3].encode("utf-8"))
            raise OSError("write failed")

    self_path = target.parent / ".2026-08-20.md.tmp"

    def fail_temp_open(path, *args, **kwargs):
        if path == self_path:
            return FailingWriter()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_temp_open)

    with pytest.raises(OSError, match="write failed"):
        write_daily_report(tmp_path, "2026-08-20", "replacement")

    assert target.read_text(encoding="utf-8") == "existing"
    assert not self_path.exists()


def test_recent_arxiv_ids_scans_only_latest_strict_report_names(tmp_path):
    reports = tmp_path / "PaperFlow" / "Reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-20.md").write_text(
        "- arxiv_id: `2608.00020`\n", encoding="utf-8"
    )
    (reports / "2026-08-19.md").write_text(
        "- arxiv_id: `2608.00019`\n", encoding="utf-8"
    )
    (reports / "2026-08-18.md").write_text(
        "- arxiv_id: `2608.00018`\n", encoding="utf-8"
    )
    (reports / "2026-99-99.md").write_text(
        "- arxiv_id: `2608.99999`\n", encoding="utf-8"
    )
    (reports / "notes.md").write_text(
        "- arxiv_id: `2608.11111`\n", encoding="utf-8"
    )

    assert recent_arxiv_ids(tmp_path, 2) == {"2608.00020", "2608.00019"}


def test_recent_arxiv_ids_matches_only_stable_field_lines(tmp_path):
    reports = tmp_path / "PaperFlow" / "Reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-20.md").write_text(
        "\n".join(
            [
                "- arxiv_id: `2608.1234`",
                "- arxiv_id: `2608.12345`",
                " - arxiv_id: `2608.11111`",
                "- arxiv_id: `2608.222222`",
                "prefix - arxiv_id: `2608.33333`",
                "- arxiv_id: 2608.44444",
            ]
        ),
        encoding="utf-8",
    )

    assert recent_arxiv_ids(tmp_path, 1) == {"2608.1234", "2608.12345"}


def test_recent_arxiv_ids_ignores_corrupt_report_without_blocking_older_files(
    tmp_path,
):
    reports = tmp_path / "PaperFlow" / "Reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-20.md").write_bytes(b"\xff\xfe")
    (reports / "2026-08-19.md").write_text(
        "- arxiv_id: `2608.00019`\n", encoding="utf-8"
    )

    assert recent_arxiv_ids(tmp_path, 2) == {"2608.00019"}


def test_recent_arxiv_ids_handles_missing_directory_and_limit_boundaries(tmp_path):
    assert recent_arxiv_ids(tmp_path, 0) == set()
    assert recent_arxiv_ids(tmp_path, 1) == set()
    with pytest.raises(ValueError, match="limit must not be negative"):
        recent_arxiv_ids(tmp_path, -1)
