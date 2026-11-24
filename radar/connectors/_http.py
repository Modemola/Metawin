"""Small stdlib HTTP client shared by the connectors: JSON in/out, retries, clear errors.

Retry policy:

* 429 Too Many Requests: wait for `Retry-After` (seconds) if present, else until
  `X-RateLimit-Reset` (unix seconds, Intercom), else exponential backoff.
* 5xx and network errors: exponential backoff (1s, 2s, 4s, ...).
* Both give up after `MAX_RETRIES` retries with a `ConnectorError`.
* 401/403: no retry; raise `ConnectorError(auth_hint)` so the user knows which
  permission the token is missing.

Error messages never include the request URL, because some APIs (Trustpilot)
take the key as a query parameter.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from email.message import Message
from typing import Any, Callable

MAX_RETRIES = 5
MAX_WAIT_SECONDS = 120.0
TIMEOUT_SECONDS = 60.0

# Indirection so tests can make retries instant.
sleep: Callable[[float], None] = time.sleep


class ConnectorError(RuntimeError):
    """A fetch failed in a way the user can act on (bad token, not found, API down)."""


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    auth_hint: str = "credentials rejected",
    not_found_hint: str | None = None,
) -> Any:
    """Send one request and return the decoded JSON response, retrying where sensible."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as e:
            status, err_headers, detail = e.code, e.headers, _error_detail(e)
            if status in (401, 403):
                raise ConnectorError(f"{auth_hint} (HTTP {status})") from None
            if status == 404 and not_found_hint:
                raise ConnectorError(not_found_hint) from None
            if status != 429 and status < 500:
                raise ConnectorError(f"API request failed: HTTP {status} {detail}".rstrip()) from None
            last = f"HTTP {status} {detail}".rstrip()
            wait = _retry_wait(err_headers, attempt) if status == 429 else _backoff(attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = f"network error: {getattr(e, 'reason', e)}"
            wait = _backoff(attempt)
        if attempt == MAX_RETRIES:
            break
        sleep(wait)
    raise ConnectorError(f"API request failed after {MAX_RETRIES} retries: {last}")


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, MAX_WAIT_SECONDS)


def _retry_wait(headers: Message | None, attempt: int, now: Callable[[], float] = time.time) -> float:
    """Seconds to wait after a 429, from the rate-limit headers if they make sense."""
    headers = headers or Message()
    retry_after = headers.get("Retry-After")
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), MAX_WAIT_SECONDS)
    reset = headers.get("X-RateLimit-Reset")
    if reset and reset.strip().isdigit():
        return min(max(float(reset) - now(), 1.0), MAX_WAIT_SECONDS)
    return _backoff(attempt)


def _error_detail(e: urllib.error.HTTPError) -> str:
    """A short, single-line excerpt of the error body (APIs put the reason there)."""
    try:
        text = e.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - best effort only
        return ""
    return " ".join(text.split())[:200]
