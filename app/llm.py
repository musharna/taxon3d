"""The one place scripts build an Anthropic client.

Twelve scripts each did `anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])` (a
KeyError with no hint) or `anthropic.Anthropic()` (the SDK's own message), and exactly one of
them — scripts/score_semantic.py — had learned that the SDK's 600 s default timeout lets a
stalled VLM connection block a batch forever. That lesson lived in one file. It lives here now:
every caller gets the 90 s per-request timeout and 3 retries, and a missing key names the
variable to set.
"""

from __future__ import annotations

import os

DEFAULT_TIMEOUT = 90.0  # the longest timeout any script had set; the SDK default is 600 s
DEFAULT_MAX_RETRIES = 3


def anthropic_client(*, timeout: float = DEFAULT_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES):
    """Return an `anthropic.Anthropic` bound to ANTHROPIC_API_KEY, or raise naming the key."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it (or add it to the repo .env) before "
            "running a script that calls the VLM judge."
        )
    import anthropic  # imported lazily: most scripts never reach the LLM path

    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=max_retries)
