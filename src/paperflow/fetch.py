from __future__ import annotations

import time

import httpx


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    attempts: int = 3,
    timeout: float = 30.0,
) -> httpx.Response:
    if type(attempts) is not int:
        raise TypeError("attempts must be an integer")
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if attempts > 3:
        raise ValueError("attempts must be at most 3")

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url, timeout=timeout)
            if _is_retryable_status(response.status_code):
                raise httpx.HTTPStatusError(
                    "recoverable response",
                    request=response.request,
                    response=response,
                )
            if response.status_code >= 600:
                raise httpx.HTTPStatusError(
                    "invalid response status",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
        except httpx.HTTPStatusError as exc:
            if not _is_retryable_status(exc.response.status_code):
                raise
            last_error = exc
        else:
            return response

        if attempt + 1 < attempts:
            time.sleep(2**attempt)

    if last_error is None:
        raise RuntimeError("request attempts exhausted without an error")
    raise last_error
