"""
Where secrets come from.

A secret in an argument is a secret in the shell history and in the
process table, so nothing in fluxrecord takes one as an argument. Every
secret comes from an environment variable, or from a file that variable
names.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["from_environment", "token_secret", "event_chain_key"]


def from_environment(name: str, file_name: str) -> str | None:
    """A secret from an environment variable, or from a file it names."""
    value = os.environ.get(name, "")
    if value:
        return value
    path = os.environ.get(file_name, "")
    if not path:
        return None
    try:
        return Path(path).expanduser().read_text().strip()
    except OSError:
        return None


def token_secret() -> str:
    """The signing secret for tokens.

    Raises:
        ValueError: no secret is configured
    """
    secret = from_environment("FLUX_TOKEN_SECRET", "FLUX_TOKEN_SECRET_FILE")
    if not secret:
        raise ValueError(
            "no signing secret: set FLUX_TOKEN_SECRET, or point FLUX_TOKEN_SECRET_FILE at a file"
        )
    return secret


def event_chain_key() -> str | None:
    """The key the event chain is signed with, when there is one."""
    return from_environment("FLUX_EVENT_KEY", "FLUX_EVENT_KEY_FILE")
