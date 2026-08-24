from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_cloud_daily_workflow_is_removed_for_local_codex_scheduling():
    assert not WORKFLOW.exists()
