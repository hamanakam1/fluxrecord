"""Subprocess fixtures use this checkout without inheriting operator state."""

import os
from pathlib import Path


def isolated_cli_environment(root: Path | None = None) -> dict[str, str]:
    """Keep the process environment while isolating FluxRecord configuration."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("FLUX_")
    }
    repo = str(Path(__file__).resolve().parent.parent)
    inherited_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{repo}{os.pathsep}{inherited_pythonpath}" if inherited_pythonpath else repo
    )
    if root is not None:
        environment["FLUX_DB"] = str(root / "ledger.sqlite")
        environment["FLUX_GITHUB_DB"] = str(root / "github.sqlite")
    return environment
