from pathlib import Path

import pytest

from paperflow.config import PaperFlowConfig


@pytest.fixture
def config(tmp_path: Path) -> PaperFlowConfig:
    vault = tmp_path / "Vault"
    vault.mkdir()
    return PaperFlowConfig(
        keywords={"robotics": 5},
        arxiv_categories=("cs.RO",),
        timezone="Asia/Hong_Kong",
        top_n=10,
        history_reports=30,
        vault_path=vault,
    )
