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
