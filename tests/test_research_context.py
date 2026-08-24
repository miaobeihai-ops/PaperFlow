from datetime import UTC, datetime
import inspect
import json
from pathlib import Path

import pytest

from paperflow.errors import ConfigError
from paperflow.research_context import inspect_context, prepare_research
from paperflow.research_models import ProviderBatch, ProviderStatus, ResearchItem, SourceRecord


NOW = datetime(2026, 8, 24, 1, 2, 3, tzinfo=UTC)


def fixed_collectors():
    record = ResearchItem(
        key="doi:10.1/example", doi="10.1/example", arxiv_id="", title="Example",
        authors=("Ada",), abstract="Abstract", published="2026-08-23", year=2026,
        url="https://doi.org/10.1/example", pdf_url="", subjects=("capture",),
        sources=(SourceRecord("crossref", "https://doi.org/10.1/example", "10.1/example"),),
    )
    return {"crossref": ProviderBatch((record,), ProviderStatus("crossref", "ok", 1))}


def test_prepare_writes_unique_contexts_below_data_root(tmp_path):
    first = prepare_research("chemical-energy", tmp_path, NOW, fixed_collectors(), Path.cwd())
    second = prepare_research("chemical-energy", tmp_path, NOW, fixed_collectors(), Path.cwd())
    assert first.context_path != second.context_path
    assert first.context_path.parts[-5:-1] == ("runs", "chemical-energy", "2026-08-24", first.run_id)
    assert first.context_path.read_bytes() == first.context_bytes
    payload = json.loads(first.context_bytes)
    assert payload["schema_version"] == 1
    assert payload["candidates"][0]["key"] == "doi:10.1/example"


def test_prepare_has_no_date_or_catch_up_input():
    assert list(inspect.signature(prepare_research).parameters) == [
        "domain", "home", "now", "collectors", "project_root"
    ]


def test_inspect_returns_bounded_summary_and_rejects_wrong_domain(tmp_path):
    run = prepare_research("chemical-energy", tmp_path, NOW, fixed_collectors(), Path.cwd())
    summary = inspect_context(run.context_path, tmp_path, "chemical-energy")
    assert summary == {
        "schema_version": 1,
        "run_id": run.run_id,
        "domain": "chemical-energy",
        "local_date": "2026-08-24",
        "candidate_count": 1,
        "provider_states": {"crossref": "ok"},
    }
    payload = json.loads(run.context_path.read_text(encoding="utf-8"))
    payload["domain"] = "robotics"
    run.context_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="context domain mismatch"):
        inspect_context(run.context_path, tmp_path, "chemical-energy")


def test_inspect_rejects_context_outside_domain_root(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError, match="context path is outside research runs"):
        inspect_context(outside, tmp_path, "chemical-energy")
