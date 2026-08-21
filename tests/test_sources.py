import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from paperflow.arxiv_source import fetch_arxiv, parse_arxiv_feed
from paperflow.fetch import request_with_retry
from paperflow.hf_source import fetch_hf_daily, fetch_hf_trending, parse_hf_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_hf_daily_fixture():
    payload = (FIXTURES / "hf_daily.json").read_text(encoding="utf-8")

    papers = parse_hf_payload(payload, "hf-daily")

    assert papers[0].arxiv_id == "2608.12345"
    assert papers[0].hf_upvotes == 12
    assert papers[0].authors == ("Ada Researcher",)
    assert papers[0].published == "2026-08-20"
    assert papers[0].url == "https://arxiv.org/abs/2608.12345"
    assert papers[0].pdf_url == "https://arxiv.org/pdf/2608.12345"


def test_parse_hf_accepts_direct_objects_and_normalizes_text():
    payload = json.dumps(
        [
            {
                "id": "https://arxiv.org/abs/2608.12345v3",
                "title": " Robotic\n  3D Reconstruction ",
                "summary": " A reconstruction\tmethod. ",
                "authors": [{"name": " Ada\n Researcher "}],
                "primaryCategory": " cs.RO ",
                "publishedAt": "2026-08-20T01:00:00.000Z",
                "upvotes": "7",
            }
        ]
    )

    paper = parse_hf_payload(payload, "hf-trending")[0]

    assert paper.title == "Robotic 3D Reconstruction"
    assert paper.abstract == "A reconstruction method."
    assert paper.authors == ("Ada Researcher",)
    assert paper.primary_category == "cs.RO"
    assert paper.hf_upvotes == 7
    assert paper.sources == ("hf-trending",)


def test_parse_hf_skips_invalid_ids_and_falls_back_to_wrapper_upvotes():
    payload = json.dumps(
        [
            {"paper": {"id": "not-an-arxiv-id"}, "upvotes": 99},
            {
                "paper": {
                    "id": "2608.12345",
                    "authors": [None, {}, {"name": " Ada Researcher "}],
                    "upvotes": "not-a-number",
                },
                "upvotes": 9,
            },
        ]
    )

    papers = parse_hf_payload(payload, "hf-daily")

    assert len(papers) == 1
    assert papers[0].authors == ("Ada Researcher",)
    assert papers[0].hf_upvotes == 9


def test_parse_hf_prefers_paper_upvotes_over_wrapper_upvotes():
    payload = json.dumps(
        [{"paper": {"id": "2608.12345", "upvotes": 4}, "upvotes": 19}]
    )

    papers = parse_hf_payload(payload, "hf-daily")

    assert papers[0].hf_upvotes == 4


@pytest.mark.parametrize(
    ("fetcher", "expected_url", "expected_source"),
    [
        (
            fetch_hf_daily,
            "https://huggingface.co/api/daily_papers?date=2026-08-20&limit=100",
            "hf-daily",
        ),
        (
            fetch_hf_trending,
            "https://huggingface.co/api/daily_papers?sort=trending&limit=50",
            "hf-trending",
        ),
    ],
)
def test_fetch_hf_uses_one_request(fetcher, expected_url, expected_source):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=[{"paper": {"id": "2608.12345"}}])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        papers = fetcher(client, date(2026, 8, 20))

    assert calls == [expected_url]
    assert papers[0].sources == (expected_source,)


def test_parse_arxiv_fixture():
    xml = (FIXTURES / "arxiv_feed.xml").read_text(encoding="utf-8")

    papers = parse_arxiv_feed(xml)

    assert papers[0].arxiv_id == "2608.12345"
    assert papers[0].primary_category == "cs.RO"
    assert papers[0].title == "Robotic 3D Reconstruction"
    assert papers[0].abstract == "A reconstruction method for mobile robots."
    assert papers[0].authors == ("Ada Researcher",)
    assert papers[0].published == "2026-08-20"
    assert papers[0].sources == ("arxiv",)
    assert papers[0].hf_upvotes == 0
    assert papers[0].url == "https://arxiv.org/abs/2608.12345"
    assert papers[0].pdf_url == "https://arxiv.org/pdf/2608.12345"


def test_parse_arxiv_normalizes_continuous_whitespace():
    xml = (FIXTURES / "arxiv_feed.xml").read_text(encoding="utf-8")
    xml = xml.replace(
        "Robotic 3D Reconstruction", " Robotic\n      3D   Reconstruction "
    ).replace("Ada Researcher", " Ada\n      Researcher ")

    paper = parse_arxiv_feed(xml)[0]

    assert paper.title == "Robotic 3D Reconstruction"
    assert paper.authors == ("Ada Researcher",)


def test_fetch_arxiv_uses_one_batched_category_request():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(
            200,
            text=(FIXTURES / "arxiv_feed.xml").read_text(encoding="utf-8"),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        papers = fetch_arxiv(
            client,
            date(2026, 8, 20),
            ("cs.RO", "cs.AI"),
        )

    assert len(calls) == 1
    query = parse_qs(urlparse(str(calls[0])).query)
    assert query == {
        "search_query": ["cat:cs.RO OR cat:cs.AI"],
        "start": ["0"],
        "max_results": ["100"],
        "sortBy": ["submittedDate"],
        "sortOrder": ["descending"],
    }
    assert papers[0].arxiv_id == "2608.12345"


def test_request_retries_429_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(429 if len(calls) < 3 else 200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", lambda _: None)

    response = request_with_retry(client, "https://example.test", attempts=3)

    assert response.text == "ok"
    assert len(calls) == 3


def test_request_retries_server_errors_with_exponential_backoff(monkeypatch):
    statuses = iter((500, 503, 200))
    sleeps = []

    def handler(request):
        return httpx.Response(next(statuses), text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", sleeps.append)

    response = request_with_retry(client, "https://example.test", attempts=3)

    assert response.text == "ok"
    assert sleeps == [1, 2]


@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.ConnectError],
)
def test_request_retries_timeout_and_network_errors(monkeypatch, error_type):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise error_type("temporary failure", request=request)
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", lambda _: None)

    response = request_with_retry(client, "https://example.test", attempts=3)

    assert response.text == "ok"
    assert calls == 3


def test_request_does_not_retry_other_client_errors(monkeypatch):
    calls = 0
    sleeps = []

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="missing")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", sleeps.append)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        request_with_retry(client, "https://example.test", attempts=3)

    assert exc_info.value.response.status_code == 404
    assert calls == 1
    assert sleeps == []


def test_request_raises_last_recoverable_exception(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(f"failure {calls}", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", lambda _: None)

    with pytest.raises(httpx.ConnectError, match="failure 3"):
        request_with_retry(client, "https://example.test", attempts=3)

    assert calls == 3


@pytest.mark.parametrize("attempts", [0, -1])
def test_request_rejects_non_positive_attempts(attempts):
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="attempts must be at least 1"):
            request_with_retry(client, "https://example.test", attempts=attempts)


@pytest.mark.parametrize("attempts", [True, 1.5, "3"])
def test_request_rejects_non_integer_attempts(attempts):
    with httpx.Client() as client:
        with pytest.raises(TypeError, match="attempts must be an integer"):
            request_with_retry(client, "https://example.test", attempts=attempts)
