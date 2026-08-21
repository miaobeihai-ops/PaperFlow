from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "daily.yml"


def test_daily_email_workflow_contract():
    content = WORKFLOW.read_text(encoding="utf-8")

    assert content.startswith("name: Daily PaperFlow email\n\non:\n")
    assert 'cron: "0 0 * * *"' in content
    assert "workflow_dispatch:" in content
    assert "permissions:\n  contents: read" in content
    assert "runs-on: ubuntu-latest" in content
    assert "timeout-minutes: 10" in content
    assert (
        "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4"
        in content
    )
    assert (
        "uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5"
        in content
    )
    assert re.search(r"uses:\s+actions/[^@\s]+@v\d+(?:\s|$)", content) is None
    assert 'python-version: "3.11"' in content
    assert "cache: pip" in content
    assert "run: python -m pip install ." in content
    assert "run: pip install ." not in content
    assert "run: paperflow --json daily --email --no-write" in content
    assert "PAPERFLOW_GMAIL_ADDRESS: ${{ secrets.PAPERFLOW_GMAIL_ADDRESS }}" in content
    assert (
        "PAPERFLOW_GMAIL_APP_PASSWORD: "
        "${{ secrets.PAPERFLOW_GMAIL_APP_PASSWORD }}" in content
    )
    assert (
        "PAPERFLOW_PRIVATE_CONFIG_JSON: "
        "${{ secrets.PAPERFLOW_PRIVATE_CONFIG_JSON }}" in content
    )

    lowered = content.casefold()
    assert "upload-artifact" not in lowered
    assert "git commit" not in lowered
    assert "git push" not in lowered
    assert "openai" not in lowered
    assert "anthropic" not in lowered
