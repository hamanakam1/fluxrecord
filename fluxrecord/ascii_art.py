"""
fluxrecord ASCII art.

One idea drives every mark here: a signal in flight. A flux waveform rides
above a half-block letterform, and the brand line locks underneath — so the
wordmark reads as work moving through a ledger rather than a static logo.

Styles:
    large    hero lockup — waveform rail + wordmark + brand line
    small    compact wordmark + brand line
    mini     one-line sparkline over the bare name
    minimal  plain text
    ascii    pure-ASCII fallback for terminals without block glyphs
"""

from __future__ import annotations

import os

__all__ = [
    "ASCII_ART",
    "ASCII_SMALL",
    "ASCII_MINI",
    "ASCII_MINIMAL",
    "ASCII_ASCII",
    "STYLES",
    "TAGLINE",
    "get_art",
    "print_art",
]

TAGLINE = "Less repeated context. More accepted work. Visible failures."

# The flux rail: one signal period tiled to span the wordmark.
_RAIL = "▅▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇█"

# Half-block letterforms (8 pixel rows -> 4 terminal rows).
_WORDMARK_LARGE: list[str] = [
    "█▀▀▀▀  █      █   █  █   █  █▀▀▀▄  █▀▀▀▀  ▄▀▀▀▀  ▄▀▀▀▄  █▀▀▀▄  █▀▀▀▄",
    "█▄▄▄   █      █   █  ▀▄ ▄▀  █▄▄▄▀  █▄▄▄   █      █   █  █▄▄▄▀  █   █",
    "█      █      █   █   ▄▀▄   █ ▀▄   █      █      █   █  █ ▀▄   █   █",
    "█      █▄▄▄▄  ▀▄▄▄▀  █   █  █   █  █▄▄▄▄  ▀▄▄▄▄  ▀▄▄▄▀  █   █  █▄▄▄▀",
]

_WORDMARK_SMALL: list[str] = [
    "█▀▀▀▀ █     █   █ █   █ █▀▀▀▄ █▀▀▀▀ ▄▀▀▀▀ ▄▀▀▀▄ █▀▀▀▄ █▀▀▀▄",
    "█▄▄▄  █     █   █ ▀▄ ▄▀ █▄▄▄▀ █▄▄▄  █     █   █ █▄▄▄▀ █   █",
    "█     █     █   █  ▄▀▄  █ ▀▄  █     █     █   █ █ ▀▄  █   █",
    "█     █▄▄▄▄ ▀▄▄▄▀ █   █ █   █ █▄▄▄▄ ▀▄▄▄▄ ▀▄▄▄▀ █   █ █▄▄▄▀",
]

# Pure-ASCII fallback, 5x5.
_WORDMARK_ASCII: list[str] = [
    "##### #.... #...# #...# ####. ##### .#### .###. ####. ####.",
    "#.... #.... #...# .#.#. #...# #.... #.... #...# #...# #...#",
    "####. #.... #...# ..#.. ####. ####. #.... #...# ####. #...#",
    "#.... #.... #...# .#.#. #.#.. #.... #.... #...# #.#.. #...#",
    "#.... ##### ##### #...# #..#. ##### .#### .###. #..#. ####.",
]

_MINI_SPARK = "▅▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇███▇▆▅▃▂▁▁▁▂▃▄▆▇███▇▆▅▃▂▁▁▁▂▃"

ASCII_ART = "\n".join([_RAIL, *_WORDMARK_LARGE, f"  {TAGLINE}"])
ASCII_SMALL = "\n".join([*_WORDMARK_SMALL, f"  {TAGLINE}"])
ASCII_MINI = "\n".join([_MINI_SPARK, "   fluxrecord"])
ASCII_MINIMAL = "fluxrecord"
ASCII_ASCII = "\n".join(_WORDMARK_ASCII)

STYLES: tuple[str, ...] = ("large", "small", "mini", "minimal", "ascii")

_PLAIN: dict[str, str] = {
    "large": ASCII_ART,
    "small": ASCII_SMALL,
    "mini": ASCII_MINI,
    "minimal": ASCII_MINIMAL,
    "ascii": ASCII_ASCII,
}

# --------------------------------------------------------------------------- #
# colour
# --------------------------------------------------------------------------- #

RGB = tuple[int, int, int]

_CYAN: RGB = (34, 211, 238)
_VIOLET: RGB = (167, 139, 250)
_RAIL_TINT: RGB = (14, 116, 144)
_SLATE: RGB = (148, 163, 184)

_RESET = "\x1b[0m"


def _rgb_to_256(rgb: RGB) -> int:
    """Map an RGB triple onto the xterm-256 palette."""
    r, g, b = rgb
    if abs(r - g) < 8 and abs(g - b) < 8:
        level = max(0, min(23, round((r - 8) / 10)))
        return 232 + level

    def quant(value: int) -> int:
        return max(0, min(5, round(value / 51)))

    return 16 + 36 * quant(r) + 6 * quant(g) + quant(b)


def _lerp(start: RGB, end: RGB, t: float) -> RGB:
    return (
        round(start[0] + (end[0] - start[0]) * t),
        round(start[1] + (end[1] - start[1]) * t),
        round(start[2] + (end[2] - start[2]) * t),
    )


def _paint(line: str, start: RGB, end: RGB) -> str:
    """Tint every visible cell of a line along a horizontal gradient."""
    visible = sum(1 for ch in line if ch != " ")
    if visible == 0:
        return line
    out: list[str] = []
    current = -1
    seen = 0
    for ch in line:
        if ch == " ":
            out.append(ch)
            continue
        t = seen / (visible - 1) if visible > 1 else 0.0
        code = _rgb_to_256(_lerp(start, end, t))
        if code != current:
            out.append(f"\x1b[38;5;{code}m")
            current = code
        out.append(ch)
        seen += 1
    return "".join(out) + _RESET


def _paint_flat(line: str, rgb: RGB) -> str:
    """Tint every visible cell of a line with one colour."""
    code = _rgb_to_256(rgb)
    return f"\x1b[38;5;{code}m{line}" + _RESET


def _colored(style: str) -> str | None:
    """Coloured variant of a style, or None when the style has no colour form."""
    if style == "large":
        rows = [
            _paint_flat(_RAIL, _RAIL_TINT),
            *[_paint(row, _CYAN, _VIOLET) for row in _WORDMARK_LARGE],
            _paint_flat(f"  {TAGLINE}", _SLATE),
        ]
    elif style == "small":
        rows = [
            *[_paint(row, _CYAN, _VIOLET) for row in _WORDMARK_SMALL],
            _paint_flat(f"  {TAGLINE}", _SLATE),
        ]
    elif style == "mini":
        rows = [_paint_flat(_MINI_SPARK, _RAIL_TINT), _paint_flat("   fluxrecord", _CYAN)]
    else:
        return None
    return "\n".join(rows)


def color_enabled() -> bool:
    """True when the environment allows ANSI colour output."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


def get_art(style: str = "large", color: bool = False) -> str:
    """Get ASCII art by style.

    Args:
        style: "large" | "small" | "mini" | "minimal" | "ascii"
        color: wrap the mark in ANSI colour (ignored when NO_COLOR is set)
    """
    if color and color_enabled():
        colored = _colored(style)
        if colored is not None:
            return colored
    return _PLAIN.get(style, ASCII_ART)


def print_art(style: str = "large", color: bool = False) -> None:
    """Print ASCII art to stdout."""
    print(get_art(style, color=color))


if __name__ == "__main__":
    import sys

    chosen = sys.argv[1] if len(sys.argv) > 1 else "large"
    paint = "color" in sys.argv[2:]
    print_art(chosen, color=paint)
