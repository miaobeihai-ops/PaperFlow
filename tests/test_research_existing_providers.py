from datetime import UTC, datetime
from pathlib import Path

import httpx

from paperflow.domain import load_domain_profile
from paperflow.providers import PROVIDERS
from paperflow.providers.arxiv import collect_arxiv
from paperflow.providers.huggingface import collect_huggingface


FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def profile():
    return load_domain_profile("robotics", project_root=Path.cwd())


def test_provider_registry_exposes_only_fixed_names():
    assert tuple(PROVIDERS) == ("arxiv", "huggingface")


def test_arxiv_adapter_returns_normalized_records_and_bounded_status():
    xml = (FIXTURES / "arxiv_feed.xml").read_text(encoding="utf-8")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=xml, request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        batch = collect_arxiv(client, profile(), now=FIXED_NOW)

    assert batch.status.name == "arxiv"
    assert batch.status.state == "ok"
    assert batch.status.count == len(batch.items) == 1
    assert batch.items[0].key == "arxiv:2608.12345"
    assert batch.items[0].year == 2026
    assert batch.items[0].subjects == ("cs.RO",)
    assert batch.items[0].sources[0].name == "arxiv"
    assert len(requests) <= len(profile().query_seeds)
    assert all(int(request.url.params["max_results"]) <= 100 for request in requests)


def test_arxiv_adapter_sanitizes_invalid_response_errors():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="PRIVATE_SENTINEL", request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        batch = collect_arxiv(client, profile(), now=FIXED_NOW)

    assert batch.items == ()
    assert batch.status.state == "failed"
    assert batch.status.message == "invalid response"
    assert "PRIVATE_SENTINEL" not in batch.status.message


def test_hf_adapter_marks_one_feed_failure_partial_without_leaking_exception():
    payload = (FIXTURES / "hf_daily.json").read_text(encoding="utf-8")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("sort") == "trending":
            return httpx.Response(404, text="PRIVATE_SENTINEL", request=request)
        return httpx.Response(200, text=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        batch = collect_huggingface(client, profile(), now=FIXED_NOW)

    assert batch.status.name == "huggingface"
    assert batch.status.state == "partial"
    assert batch.status.count == len(batch.items) == 1
    assert batch.status.message == "request failed"
    assert "PRIVATE_SENTINEL" not in batch.status.message
    assert batch.items[0].sources[0].name == "hf-daily"
