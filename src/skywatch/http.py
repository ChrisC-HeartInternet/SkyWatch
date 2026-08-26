"""HTTP fetching with retries and exponential backoff.

One shared client, tuned for polite use of free public APIs: generous timeouts,
a few retries on transient failures, and an identifying User-Agent.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from skywatch import console

USER_AGENT = "skywatch/0.1 (personal weather analysis; github.com/n/a)"

# Transient statuses worth retrying; 4xx (except 429) are not.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BASE_DELAY_S = 1.5


def get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
    """GET a JSON document with retries. Raises httpx.HTTPError after exhausting retries."""
    return _get(url, params, timeout).json()


def get_text(url: str, params: dict[str, Any] | None = None, timeout: float = 60.0) -> str:
    """GET a text document with retries."""
    return _get(url, params, timeout).text


def _get(url: str, params: dict[str, Any] | None, timeout: float) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            if resp.status_code in _RETRY_STATUSES:
                raise httpx.HTTPStatusError(
                    f"retryable status {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code not in _RETRY_STATUSES
            ):
                raise
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                break
            delay = _BASE_DELAY_S * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            console.log().warning(
                "GET %s failed (%s); retry %d/%d in %.1fs",
                url, type(exc).__name__, attempt, _MAX_ATTEMPTS - 1, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
