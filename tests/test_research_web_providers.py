from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx

from paperflow.domain import load_domain_profile
from paperflow.providers.crossref import collect_crossref
from paperflow.providers.feed import collect_feed
from paperflow.providers.openalex import collect_openalex


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def profile():
    base = load_domain_profile("chemical-energy", project_root=Path.cwd())
    return replace(base, query_seeds=("carbon capture",), candidate_limit=100)


def fixture_client(filename: str, requests: list[httpx.Request]):
    body = (FIXTURES / filename).read_text(encoding="utf-8")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_crossref_query_is_encoded_bounded_and_parsed():
    requests: list[httpx.Request] = []
    with fixture_client("crossref_response.json", requests) as client:
        batch = collect_crossref(client, profile(), now=NOW)
    request = requests[0]
    assert request.url == httpx.URL("https://api.crossref.org/works", params=request.url.params)
    assert request.url.params["rows"] == "100"
    assert request.url.params["filter"].startswith(f"from-pub-date:{(NOW - timedelta(hours=profile().lookback_hours)).date()},")
    assert request.url.params["filter"].endswith(f",until-pub-date:{NOW.date()}")
    assert batch.status.state == "ok"
    assert len(batch.items) == 1
    assert batch.items[0].doi == "10.1000/example"
    assert batch.items[0].abstract == "A useful abstract."


def test_openalex_uses_fixed_endpoint_and_parses_record():
    requests: list[httpx.Request] = []
    with fixture_client("openalex_response.json", requests) as client:
        batch = collect_openalex(client, profile(), now=NOW)
    assert requests[0].url.host == "api.openalex.org"
    assert requests[0].url.params["per-page"] == "100"
    assert requests[0].url.params["filter"].endswith(f",to_publication_date:{NOW.date()}")
    assert len(batch.items) == 1
    assert batch.items[0].sources[0].external_id == "W123"
    assert batch.items[0].authors == ("Ada Researcher",)


def test_feed_parses_atom_as_plain_text_and_is_bounded():
    requests: list[httpx.Request] = []
    configured = replace(profile(), feeds=("https://example.test/feed.xml",))
    with fixture_client("research_feed.xml", requests) as client:
        batch = collect_feed(client, configured, now=NOW)
    assert len(requests) == 1
    assert batch.status.state == "ok"
    assert batch.items[0].title == "National hydrogen policy update"
    assert batch.items[0].abstract == "Policy details and targets."
    assert "<b>" not in batch.items[0].abstract


def test_malformed_top_level_payload_fails_only_provider_without_echo():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"PRIVATE_SENTINEL": []}), request=request)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        batch = collect_crossref(client, profile(), now=NOW)
    assert batch.items == ()
    assert batch.status.state == "failed"
    assert batch.status.message == "invalid response"
