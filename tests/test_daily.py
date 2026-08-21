from dataclasses import replace

import httpx
import pytest

from paperflow.config import ConfigError
from paperflow.daily import AllSourcesFailed, run_daily
from paperflow.models import Paper


def sample(source: str) -> Paper:
    return Paper(
        arxiv_id="2608.12345",
        title="Robotics",
        authors=("Ada",),
        abstract="robotics",
        primary_category="cs.RO",
        published="2026-08-20",
        sources=(source,),
        hf_upvotes=0,
        url="https://arxiv.org/abs/2608.12345",
        pdf_url="https://arxiv.org/pdf/2608.12345",
    )


def test_daily_keeps_partial_results(config, monkeypatch):
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_daily", lambda *_: [sample("hf-daily")]
    )
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_trending",
        lambda *_: (_ for _ in ()).throw(TimeoutError("slow")),
    )
    monkeypatch.setattr(
        "paperflow.daily.fetch_arxiv", lambda *_: [sample("arxiv")]
    )

    result = run_daily(config, "2026-08-20", write_report=False)

    assert len(result.papers) == 1
    assert result.papers[0].paper.sources == ("arxiv", "hf-daily")
    assert result.failures[0].source == "hf-trending"


def test_daily_raises_when_every_source_fails(config, monkeypatch):
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(
            f"paperflow.daily.{name}",
            lambda *_: (_ for _ in ()).throw(TimeoutError("down")),
        )

    with pytest.raises(AllSourcesFailed) as exc_info:
        run_daily(config, "2026-08-20", write_report=False)

    assert [failure.source for failure in exc_info.value.failures] == [
        "hf-daily",
        "hf-trending",
        "arxiv",
    ]


def test_daily_calls_each_source_exactly_once(config, monkeypatch):
    calls = {"hf-daily": 0, "hf-trending": 0, "arxiv": 0}

    def fetch(source):
        def execute(*_):
            calls[source] += 1
            return []

        return execute

    monkeypatch.setattr("paperflow.daily.fetch_hf_daily", fetch("hf-daily"))
    monkeypatch.setattr("paperflow.daily.fetch_hf_trending", fetch("hf-trending"))
    monkeypatch.setattr("paperflow.daily.fetch_arxiv", fetch("arxiv"))

    run_daily(config, "2026-08-20", write_report=False)

    assert calls == {"hf-daily": 1, "hf-trending": 1, "arxiv": 1}


def test_daily_does_not_overwrite_existing_report_when_all_sources_fail(
    config, monkeypatch
):
    report = config.vault_path / "PaperFlow" / "Reports" / "2026-08-20.md"
    report.parent.mkdir(parents=True)
    report.write_text("existing", encoding="utf-8")
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(
            f"paperflow.daily.{name}",
            lambda *_: (_ for _ in ()).throw(TimeoutError("down")),
        )

    with pytest.raises(AllSourcesFailed):
        run_daily(config, "2026-08-20")

    assert report.read_text(encoding="utf-8") == "existing"


def test_daily_excludes_history_only_when_writing_locally(config, monkeypatch):
    reports = config.vault_path / "PaperFlow" / "Reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-19.md").write_text(
        "- arxiv_id: `2608.12345`\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_daily", lambda *_: [sample("hf-daily")]
    )
    monkeypatch.setattr("paperflow.daily.fetch_hf_trending", lambda *_: [])
    monkeypatch.setattr("paperflow.daily.fetch_arxiv", lambda *_: [])

    preview = run_daily(config, "2026-08-20", write_report=False)
    local = run_daily(config, "2026-08-20", write_report=True)

    assert [item.paper.arxiv_id for item in preview.papers] == ["2608.12345"]
    assert local.papers == ()
    assert local.report_path == reports / "2026-08-20.md"
    assert local.report_path.is_file()


def test_daily_limits_ranked_results_to_top_n(config, monkeypatch):
    lower = replace(
        sample("arxiv"),
        arxiv_id="2608.12346",
        title="Unrelated",
        abstract="none",
        primary_category="cs.AI",
    )
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_daily", lambda *_: [lower, sample("hf-daily")]
    )
    monkeypatch.setattr("paperflow.daily.fetch_hf_trending", lambda *_: [])
    monkeypatch.setattr("paperflow.daily.fetch_arxiv", lambda *_: [])

    result = run_daily(
        replace(config, top_n=1), "2026-08-20", write_report=False
    )

    assert [item.paper.arxiv_id for item in result.papers] == ["2608.12345"]


def test_daily_treats_empty_source_results_as_success(config, monkeypatch):
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(f"paperflow.daily.{name}", lambda *_: [])

    result = run_daily(config, "2026-08-20", write_report=False)

    assert result.papers == ()
    assert result.failures == ()


def test_daily_closes_created_http_client(config, monkeypatch):
    client = httpx.Client()
    monkeypatch.setattr("paperflow.daily.httpx.Client", lambda: client)
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(f"paperflow.daily.{name}", lambda *_: [])

    run_daily(config, "2026-08-20", write_report=False)

    assert client.is_closed


@pytest.mark.parametrize(
    "invalid_date",
    ["2026-8-20", "2026-08-20T00:00:00", "2026-W34-4", "not-a-date"],
)
def test_daily_rejects_dates_outside_strict_yyyy_mm_dd(
    config, monkeypatch, invalid_date
):
    monkeypatch.setattr(
        "paperflow.daily.httpx.Client",
        lambda: pytest.fail("client must not be created for an invalid date"),
    )

    with pytest.raises(ConfigError, match="date must use YYYY-MM-DD"):
        run_daily(config, invalid_date, write_report=False)


def test_daily_requires_vault_only_when_writing(config, monkeypatch):
    cloud_config = replace(config, vault_path=None)
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(f"paperflow.daily.{name}", lambda *_: [])

    preview = run_daily(cloud_config, "2026-08-20", write_report=False)
    with pytest.raises(ConfigError, match="vault_path is required"):
        run_daily(cloud_config, "2026-08-20", write_report=True)

    assert preview.report_path is None


def test_daily_does_not_swallow_base_exceptions(config, monkeypatch):
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_daily",
        lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        run_daily(config, "2026-08-20", write_report=False)


def test_daily_compacts_failure_messages(config, monkeypatch):
    monkeypatch.setattr("paperflow.daily.fetch_hf_daily", lambda *_: [])
    monkeypatch.setattr(
        "paperflow.daily.fetch_hf_trending",
        lambda *_: (_ for _ in ()).throw(RuntimeError("  first\n  second  ")),
    )
    monkeypatch.setattr("paperflow.daily.fetch_arxiv", lambda *_: [])

    result = run_daily(config, "2026-08-20", write_report=False)

    assert result.failures[0].message == "first second"
