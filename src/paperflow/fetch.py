from __future__ import annotations

import time

import httpx


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

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    "recoverable response",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 and exc.response.status_code < 500:
                raise
            last_error = exc
        else:
            return response

        if attempt + 1 < attempts:
            time.sleep(2**attempt)

    if last_error is None:
        raise RuntimeError("request attempts exhausted without an error")
    raise last_error
