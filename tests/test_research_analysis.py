import copy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperflow.errors import ConfigError
from paperflow.research_analysis import validate_analysis
from paperflow.research_context import prepare_research
from paperflow.research_models import ProviderBatch, ProviderStatus, ResearchItem, SourceRecord


def context_and_analysis(tmp_path, domain="chemical-energy"):
    item = ResearchItem("doi:10.1/x", "10.1/x", "", "Title", ("Ada",), "Abstract", "2026-08-24", 2026, "https://doi.org/10.1/x", "", (), (SourceRecord("crossref", "https://doi.org/10.1/x", "10.1/x"),))
    run = prepare_research(domain, tmp_path, datetime(2026, 8, 24, tzinfo=UTC), {"crossref": ProviderBatch((item,), ProviderStatus("crossref", "ok", 1))}, Path.cwd())
    analysis = {
        "schema_version": 1, "run_id": run.run_id, "domain": domain,
        "generated_at": "2026-08-24T01:00:00Z", "coverage": "Good public coverage",
        "additional_queries": ["membrane deployment"],
        "selected": [{
            "candidate_id": "doi:10.1/x", "analysis_depth": "abstract",
            "relevance": 9, "novelty": 7, "evidence_quality": 6, "industrial_value": 8,
            "confidence": "medium", "reason": "Relevant", "method": "Method",
            "evidence": "Evidence", "limitations": "Limit", "practical_implications": "Action",
            "citations": [{"candidate_id": "doi:10.1/x", "url": "https://doi.org/10.1/x"}],
        }],
        "themes": ["Theme"], "disagreements": [], "policy_industry_links": ["Link"],
        "actions": ["Act"], "source_limitations": ["Public metadata only"],
        "unresolved_questions": ["Question"],
    }
    return run.context_path, analysis


def test_analysis_rejects_unknown_candidate_and_unknown_fields(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    unknown = copy.deepcopy(analysis)
    unknown["selected"][0]["candidate_id"] = "unknown"
    with pytest.raises(ConfigError, match="analysis references an unknown candidate"):
        validate_analysis(context, unknown)
    altered = copy.deepcopy(analysis)
    altered["selected"][0]["title"] = "Fabricated metadata"
    with pytest.raises(ConfigError, match="analysis fields are invalid"):
        validate_analysis(context, altered)


def test_analysis_rejects_unknown_url_and_invalid_score(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    analysis["selected"][0]["citations"][0]["url"] = "https://fake.test"
    with pytest.raises(ConfigError, match="citation URL is not present"):
        validate_analysis(context, analysis)
    context, analysis = context_and_analysis(tmp_path / "other")
    analysis["selected"][0]["relevance"] = 11
    with pytest.raises(ConfigError, match="scores must be integers from 0 to 10"):
        validate_analysis(context, analysis)


def test_analysis_accepts_full_text_evidence_inside_run_directory(tmp_path):
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

    validated = validate_analysis(context, analysis)

    assert validated["selected"][0]["full_text_file"] == "full-text/paper.pdf"
    assert validated["selected"][0]["figures"][0]["page"] == 2


def test_analysis_rejects_unproven_or_unsafe_full_text_evidence(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    analysis["selected"][0].update({
        "analysis_depth": "full_text",
        "access_status": "open_access",
        "full_text_file": "full-text/missing.pdf",
        "figures": [],
    })
    with pytest.raises(ConfigError, match="full-text evidence is invalid"):
        validate_analysis(context, analysis)

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    analysis["selected"][0]["full_text_file"] = "../../../../outside.pdf"
    with pytest.raises(ConfigError, match="full-text evidence is invalid"):
        validate_analysis(context, analysis)


def test_analysis_rejects_figures_without_full_text_and_more_than_three(tmp_path):
    context, analysis = context_and_analysis(tmp_path)
    run_dir = context.parent
    figures = []
    for index in range(4):
        path = run_dir / "figures" / f"figure-{index}.png"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        figures.append({
            "file": f"figures/figure-{index}.png",
            "figure": f"Figure {index}",
            "page": index + 1,
            "caption": "Evidence",
            "source_url": "https://doi.org/10.1/x",
            "license": "CC BY 4.0",
        })
    analysis["selected"][0].update({
        "access_status": "abstract_only",
        "full_text_file": "",
        "figures": figures[:1],
    })
    with pytest.raises(ConfigError, match="figures require full-text analysis"):
        validate_analysis(context, analysis)

    pdf = run_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    analysis["selected"][0].update({
        "analysis_depth": "full_text",
        "access_status": "open_access",
        "full_text_file": "paper.pdf",
        "figures": figures,
    })
    with pytest.raises(ConfigError, match="at most three figures"):
        validate_analysis(context, analysis)
