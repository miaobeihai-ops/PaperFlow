from paperflow.research_report import finalize_research
from test_research_analysis import context_and_analysis


def test_finalize_writes_domain_json_and_markdown_atomically(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    analysis["actions"] = ["Global action only"]
    result = finalize_research(context, analysis, home=tmp_path)
    assert result.markdown_path == tmp_path / "reports/chemical-energy/2026-08-24.md"
    assert result.json_path == tmp_path / "reports/chemical-energy/2026-08-24.json"
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("---\n")
    assert "Title" in markdown
    assert "检索时段: 2026-08-22 08:00 - 2026-08-24 08:00 (Asia/Hong_Kong)" in markdown
    assert "## 建议行动" not in markdown
    assert "## 来源局限" not in markdown
    assert "Global action only" not in markdown
    assert "Public metadata only" not in markdown
    assert markdown.index("原文: https://doi.org/10.1/x") < markdown.index("阅读: `abstract` / `abstract_only`")
    assert markdown.index("阅读: `abstract` / `abstract_only`") < markdown.index("证据边界: Limit")
    assert not list(result.markdown_path.parent.glob("*.tmp"))


def test_finalize_keeps_domain_reports_independent(tmp_path):
    chemical_context, chemical_analysis = context_and_analysis(tmp_path, "chemical-energy")
    robotics_context, robotics_analysis = context_and_analysis(tmp_path, "robotics")
    chemical = finalize_research(chemical_context, chemical_analysis, home=tmp_path)
    robotics = finalize_research(robotics_context, robotics_analysis, home=tmp_path)
    assert chemical.markdown_path != robotics.markdown_path
    assert chemical.markdown_path.exists() and robotics.markdown_path.exists()


def test_finalize_copies_and_attributes_full_text_figures(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    run_dir = context.parent
    pdf = run_dir / "full-text" / "paper.pdf"
    figure = run_dir / "figures" / "figure-1.png"
    pdf.parent.mkdir()
    figure.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4\n")
    figure.write_bytes(b"\x89PNG\r\n\x1a\n")
    analysis["selected"][0].update({
        "analysis_depth": "full_text",
        "access_status": "open_access",
        "full_text_file": "full-text/paper.pdf",
        "figures": [{
            "file": "figures/figure-1.png",
            "figure": "Figure 1",
            "page": 2,
            "caption": "Cell architecture",
            "source_url": "https://doi.org/10.1/x",
            "license": "CC BY 4.0",
        }],
    })

    result = finalize_research(context, analysis, home=tmp_path)

    copied = result.markdown_path.parent / "assets" / "2026-08-24" / "doi-10-1-x-figure-1.png"
    assert copied.read_bytes() == figure.read_bytes()
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "![Figure 1 - Cell architecture](assets/2026-08-24/doi-10-1-x-figure-1.png)" in markdown
    assert "Figure 1；PDF第2页；CC BY 4.0；https://doi.org/10.1/x" in markdown
