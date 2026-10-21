"""
fluxrecord ASCII Art
"""
from typing import List

ASCII_ART = r"""
    ███████╗ ██████╗ ██╗      ██╗ ██████╗ ███████╗███████╗███████╗██████╗ ██████╗ ██████╗
    ██╔════╝██╔═══██╗██║      ██║██╔═══██╗██╔════╝██╔════╝██╔════╝██╔══██╗██╔══██╗██╔══██╗
    ███████╗██║   ██║██║      ██║██║   ██║███████╗█████╗  █████╗  ██████╔╝██████╔╝██████╔╝
    ╚════██║██║   ██║██║      ██║██║   ██║╚════██║██╔══╝  ██╔══╝  ██╔══██╗██╔══██╗██╔══██║
    ███████║╚██████╔╝███████╗██║╚██████╔╝███████║███████╗███████╗██║  ██║██║  ██║██████╔╝
    ╚══════╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝ ╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═════╝

      fluxrecord — Less repeated context. More accepted work. Visible failures.
"""

ASCII_SMALL = r"""
███████ ██████  ██      ██ ██████ ███████ ███████ ██████ ██████ ██████
██      ██   ██ ██      ██ ██   ██ ██      ██      ██   ██ ██   ██ ██   ██
███████ ██████  ██      ██ ██████ █████   █████   ██████  ██████ ██████
██      ██   ██ ██      ██ ██   ██ ██      ██      ██   ██ ██   ██ ██   ██
███████ ██   ██ ███████ ██ ██   ██ ███████ ███████ ██   ██ ██   ██ ██   ██

    fluxrecord — Less repeated context. More accepted work. Visible failures.
"""

ASCII_MINI = r"""
  ▄▄▄▄▄▄▄  ▄▄▄▄▄▄  ▄▄      ▄▄  ▄▄▄▄▄▄  ▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄▄▄▄▄ ▄▄▄▄▄▄
  ▄▄      ▄▄   ▄▄ ▄▄      ▄▄ ▄▄   ▄▄ ▄▄      ▄▄      ▄▄   ▄▄ ▄▄   ▄▄ ▄▄   ▄▄
  ▄▄▄▄▄▄  ▄▄      ▄▄      ▄▄ ▄▄▄▄▄▄ ▄▄▄▄▄▄  ▄▄▄▄▄▄  ▄▄▄▄▄▄ ▄▄▄▄▄▄ ▄▄   ▄▄
  ▄▄      ▄▄   ▄▄ ▄▄      ▄▄ ▄▄   ▄▄ ▄▄      ▄▄      ▄▄   ▄▄ ▄▄   ▄▄ ▄▄   ▄▄
  ▄▄      ▄▄   ▄▄ ▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄   ▄▄ ▄▄   ▄▄ ▄▄   ▄▄
"""

ASCII_MINIMAL = "fluxrecord"


def get_art(style: str = "large") -> str:
    """Get ASCII art by style.
    
    Args:
        style: "large" | "small" | "mini" | "minimal"
    """
    styles = {
        "large": ASCII_ART,
        "small": ASCII_SMALL,
        "mini": ASCII_MINI,
        "minimal": ASCII_MINIMAL,
    }
    return styles.get(style, ASCII_ART)


def print_art(style: str = "large") -> None:
    """Print ASCII art to stdout."""
    print(get_art(style))


if __name__ == "__main__":
    import sys
    style = sys.argv[1] if len(sys.argv) > 1 else "large"
    print_art(style)