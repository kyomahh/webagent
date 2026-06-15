"""Shared retry helpers for LLM calls.

The retry policy is intentionally provider-agnostic: it relies on structured
exception attributes such as HTTP status code instead of matching provider
error messages.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_transient_llm_error(exc: BaseException) -> bool:
    status_code = _status_code(exc)
    if status_code in TRANSIENT_STATUS_CODES:
        return True

    # Some wrappers normalize provider errors without preserving status_code.
    name = exc.__class__.__name__.lower()
    return "ratelimit" in name or "rate_limit" in name or "timeout" in name


def invoke_with_backoff(
    llm: Any,
    messages: list[Any],
    *,
    operation: str,
    max_attempts: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
) -> Any:
    """Invoke a LangChain-compatible LLM with bounded exponential backoff."""

    attempts = max_attempts or int(os.environ.get("WEBAGENT_LLM_MAX_ATTEMPTS", "4"))
    delay = base_delay or float(os.environ.get("WEBAGENT_LLM_RETRY_BASE_SECONDS", "1.5"))
    delay_cap = max_delay or float(os.environ.get("WEBAGENT_LLM_RETRY_MAX_SECONDS", "45"))
    attempts = max(1, attempts)

    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - wrappers expose mixed exception types
            last_exc = exc
            if attempt >= attempts or not is_transient_llm_error(exc):
                raise

            sleep_seconds = min(delay_cap, delay * (2 ** (attempt - 1)))
            sleep_seconds += random.uniform(0, min(0.5, sleep_seconds * 0.1))
            print(
                f"[LLMRetry] {operation} transient failure "
                f"({exc.__class__.__name__}); retry {attempt}/{attempts} "
                f"in {sleep_seconds:.1f}s"
            )
            time.sleep(sleep_seconds)

    assert last_exc is not None
    raise last_exc
