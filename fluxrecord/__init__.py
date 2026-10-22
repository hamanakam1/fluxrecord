"""fluxrecord — Local coordination ledger."""

from __future__ import annotations

from .core import Conflict, Ledger
from .wire import WIRE_FORMAT

__all__ = ["Ledger", "Conflict", "WIRE_FORMAT"]
__version__ = "0.2.0"

#: The wire format this package speaks. Also available as ``WIRE_FORMAT``.
__wire_format__ = WIRE_FORMAT
