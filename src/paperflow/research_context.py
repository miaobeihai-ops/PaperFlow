from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from paperflow.domain import load_domain_profile
from paperflow.errors import ConfigError
from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import ProviderBatch

_SCHEMA_VERSION = 1
_LOCAL_ZONE = ZoneInfo("Asia/Hong_Kong")
_MAX_CONTEXT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class PreparedResearch:
    run_id: str
    domain: str
    local_date: str
    context_path: Path
    context_bytes: bytes
    candidate_count: int
    partial: bool


def _collect_batches(profile, now: datetime, collectors: Mapping[str, object]) -> list[ProviderBatch]:
    batches: list[ProviderBatch] = []
    with httpx.Client(headers={"User-Agent": "PaperFlow/0.1"}) as client:
        for name in profile.providers:
            collector = collectors.get(name)
            if collector is None:
                continue
            if isinstance(collector, ProviderBatch):
                batch = collector
            elif callable(collector):
                batch = collector(client, profile, now=now)
            else:
                raise ConfigError("research collector is invalid")
            if not isinstance(batch, ProviderBatch) or batch.status.name != name:
                raise ConfigError("research collector returned an invalid batch")
            batches.append(batch)
    return batches


def prepare_research(
    domain: str,
    home: Path,
    now: datetime,
    collectors: Mapping[str, object],
    project_root: Path,
) -> PreparedResearch:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ConfigError("research time must include a timezone")
    data_root = Path(home)
    overlay = data_root / "config" / "domains" / f"{domain}.local.toml"
    profile = load_domain_profile(
        domain,
        project_root=Path(project_root),
        overlay_path=overlay if overlay.exists() else None,
    )
    batches = _collect_batches(profile, now, collectors)
    candidates = deduplicate_research_items(
        [item for batch in batches for item in batch.items]
    )[: profile.candidate_limit]
    run_id = str(uuid4())
    local_date = now.astimezone(_LOCAL_ZONE).date().isoformat()
    local_end = now.astimezone(_LOCAL_ZONE)
    local_start = local_end - timedelta(hours=profile.lookback_hours)
    run_dir = data_root / "runs" / domain / local_date / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    context_path = run_dir / "context.json"
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "domain": domain,
        "local_date": local_date,
        "generated_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "search_window": {
            "started_at": local_start.isoformat(timespec="seconds"),
            "ended_at": local_end.isoformat(timespec="seconds"),
            "timezone": _LOCAL_ZONE.key,
        },
        "profile": asdict(profile),
        "provider_statuses": [asdict(batch.status) for batch in batches],
        "candidates": [asdict(item) for item in candidates],
    }
    context_bytes = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with context_path.open("xb") as handle:
        handle.write(context_bytes)
    return PreparedResearch(
        run_id=run_id,
        domain=domain,
        local_date=local_date,
        context_path=context_path,
        context_bytes=context_bytes,
        candidate_count=len(candidates),
        partial=any(batch.status.state in {"partial", "failed"} for batch in batches),
    )


def _safe_context_path(context_path: Path, home: Path, domain: str) -> Path:
    path = Path(context_path)
    allowed = (Path(home) / "runs" / domain).resolve()
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(allowed)
    except (OSError, ValueError) as exc:
        raise ConfigError("context path is outside research runs") from exc
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise ConfigError("context path must not contain symlinks")
        if current.resolve() == allowed:
            break
        current = current.parent
    if not resolved.is_file() or resolved.name != "context.json":
        raise ConfigError("context file is invalid")
    return resolved


def inspect_context(context_path: Path, home: Path, domain: str) -> dict[str, object]:
    path = _safe_context_path(context_path, home, domain)
    try:
        if path.stat().st_size > _MAX_CONTEXT_BYTES:
            raise ConfigError("context file is too large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("context file is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
        raise ConfigError("context schema mismatch")
    if payload.get("domain") != domain:
        raise ConfigError("context domain mismatch")
    candidates = payload.get("candidates")
    statuses = payload.get("provider_statuses")
    if not isinstance(candidates, list) or not isinstance(statuses, list):
        raise ConfigError("context file is invalid")
    provider_states = {
        status["name"]: status["state"]
        for status in statuses
        if isinstance(status, dict) and isinstance(status.get("name"), str) and isinstance(status.get("state"), str)
    }
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": payload.get("run_id"),
        "domain": domain,
        "local_date": payload.get("local_date"),
        "candidate_count": len(candidates),
        "provider_states": provider_states,
    }
