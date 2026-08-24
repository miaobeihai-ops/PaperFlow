from paperflow.research_report import finalize_research
from test_research_analysis import context_and_analysis


def test_finalize_writes_domain_json_and_markdown_atomically(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    result = finalize_research(context, analysis, home=tmp_path)
    assert result.markdown_path == tmp_path / "reports/chemical-energy/2026-08-24.md"
    assert result.json_path == tmp_path / "reports/chemical-energy/2026-08-24.json"
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("---\n")
    assert "Title" in markdown
    assert "Action" in markdown
    assert not list(result.markdown_path.parent.glob("*.tmp"))


def test_finalize_keeps_domain_reports_independent(tmp_path):
    chemical_context, chemical_analysis = context_and_analysis(tmp_path, "chemical-energy")
    robotics_context, robotics_analysis = context_and_analysis(tmp_path, "robotics")
    chemical = finalize_research(chemical_context, chemical_analysis, home=tmp_path)
    robotics = finalize_research(robotics_context, robotics_analysis, home=tmp_path)
    assert chemical.markdown_path != robotics.markdown_path
    assert chemical.markdown_path.exists() and robotics.markdown_path.exists()
