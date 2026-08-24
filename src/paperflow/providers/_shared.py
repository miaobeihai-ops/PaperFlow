from __future__ import annotations

import html
import re

import httpx

_TAG = re.compile(r"<[^>]*>")


def plain_text(value: object) -> str:
    decoded = html.unescape(_TAG.sub(" ", str(value or "")))
    return " ".join(decoded.split())


def safe_error(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "network error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "request failed"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid response"
    return "provider error"
