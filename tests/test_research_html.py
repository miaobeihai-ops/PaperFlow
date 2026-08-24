from paperflow.research_report import finalize_research
from test_research_analysis import context_and_analysis


def test_finalize_writes_self_contained_printable_html(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    analysis["actions"] = ["Global action only"]

    result = finalize_research(context, analysis, home=tmp_path)

    assert result.html_path == tmp_path / "reports/chemical-energy/2026-08-24.html"
    html = result.html_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in html.lower()
    assert "@page" in html
    assert "2026-08-22 08:00 - 2026-08-24 08:00" in html
    assert "Asia/Hong_Kong" in html
    assert "建议行动" not in html
    assert "来源局限" not in html
    assert "Global action only" not in html
    assert "https://doi.org/10.1/x" in html
    assert "<script" not in html.lower()


def test_html_embeds_figure_bytes(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    run_dir = context.parent
    pdf = run_dir / "paper.pdf"
    figure = run_dir / "figure.png"
    pdf.write_bytes(b"%PDF-1.4\n")
    figure.write_bytes(b"\x89PNG\r\n\x1a\n")
    analysis["selected"][0].update({
        "analysis_depth": "full_text",
        "access_status": "open_access",
        "full_text_file": "paper.pdf",
        "figures": [{
            "file": "figure.png",
            "figure": "Figure 1",
            "page": 2,
            "caption": "Cell architecture",
            "source_url": "https://doi.org/10.1/x",
            "license": "CC BY 4.0",
        }],
    })

    result = finalize_research(context, analysis, home=tmp_path)

    html = result.html_path.read_text(encoding="utf-8")
    assert "data:image/png;base64,iVBORw0KGgo=" in html
    assert "Figure 1" in html
    assert "PDF第2页" in html
