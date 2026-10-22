"""
An optional tokenizer.

The core stays stdlib-only: a tokenizer is a plug-in, not a dependency.
Nothing here imports a tokenizer, and nothing here requires one.

When a tokenizer is registered, packets report a token count. When none
is, the field is absent and the byte budget is the authority — which is
what the specification says it is.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["Tokenizer", "register", "clear", "count", "current", "registered"]

#: A tokenizer takes text and returns the number of tokens in it.
Tokenizer = Callable[[str], int]

_TOKENIZER: Tokenizer | None = None


def register(tokenizer: Tokenizer) -> None:
    """Register the tokenizer that packets will be measured with."""
    global _TOKENIZER
    if not callable(tokenizer):
        raise ValueError("a tokenizer must be callable")
    _TOKENIZER = tokenizer


def clear() -> None:
    """Forget the registered tokenizer."""
    global _TOKENIZER
    _TOKENIZER = None


def registered() -> bool:
    """Whether a tokenizer is registered."""
    return _TOKENIZER is not None


def current() -> Tokenizer | None:
    """The registered tokenizer, or None. This is what a caller passes in."""
    return _TOKENIZER


def count(text: str) -> int | None:
    """The number of tokens in the text, or None when no tokenizer is registered.

    None means "not measured", not "zero".
    """
    if _TOKENIZER is None:
        return None
    return int(_TOKENIZER(text))
