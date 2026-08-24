from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

from paperflow.errors import ConfigError


_WINDOWS_BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def find_browser(candidates: list[Path] | tuple[Path, ...] = _WINDOWS_BROWSERS) -> Path:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    raise ConfigError("Chrome or Edge is required for PDF export")


def export_html_to_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    temp_root: Path,
    browser: Path | None = None,
    runner=subprocess.run,
) -> Path:
    html_path = Path(html_path).resolve(strict=True)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    browser = Path(browser) if browser is not None else find_browser()
    profile = Path(tempfile.mkdtemp(prefix="paperflow-pdf-", dir=temp_root))
    temporary_pdf = pdf_path.parent / f".{pdf_path.stem}.{uuid4().hex}.tmp.pdf"
    command = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--allow-file-access-from-files",
        f"--user-data-dir={profile}",
        f"--print-to-pdf={temporary_pdf}",
        html_path.as_uri(),
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if completed.returncode != 0 or not temporary_pdf.is_file() or temporary_pdf.stat().st_size < 9:
            raise ConfigError("PDF export failed")
        os.replace(temporary_pdf, pdf_path)
        return pdf_path
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError("PDF export failed") from exc
    finally:
        if temporary_pdf.exists():
            temporary_pdf.unlink()
        shutil.rmtree(profile, ignore_errors=True)


__all__ = ["export_html_to_pdf", "find_browser"]
