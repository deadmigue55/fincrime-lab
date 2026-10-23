"""Project paths and credentials.

Everything that touches the filesystem or secrets goes through here, so the rest of the
code never hard-codes a path. Set ``FCL_DATA_DIR`` to keep the data somewhere else
(e.g. a bigger drive: the largest public benchmark is tens of GB). Secrets are read from
environment variables, which can be put in a local ``.env`` file (see ``.env.example``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` until a folder containing ``pyproject.toml`` is found."""
    here = (start or Path(__file__)).resolve()
    for folder in (here, *here.parents):
        if (folder / "pyproject.toml").is_file():
            return folder
    raise FileNotFoundError("Could not find the project root (no pyproject.toml above here).")


PROJECT_ROOT = find_project_root()
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Paths:
    """Standard locations for data and outputs."""

    root: Path
    data: Path

    @property
    def raw(self) -> Path:
        """Downloaded source files, exactly as published."""
        return self.data / "raw"

    @property
    def processed(self) -> Path:
        """Parquet lake: cleaned, partitioned, analysis-ready files."""
        return self.data / "processed"

    @property
    def database(self) -> Path:
        """Local DuckDB file holding the marts."""
        return self.data / "fincrime.duckdb"

    @property
    def manifest(self) -> Path:
        """Log of every downloaded file (source, URL, date, checksum)."""
        return self.raw / "MANIFEST.csv"

    def ensure(self) -> Paths:
        """Create the data folders if they don't exist yet."""
        self.raw.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        return self


def get_paths() -> Paths:
    """Resolve project paths, honouring the ``FCL_DATA_DIR`` override."""
    data_dir = os.environ.get("FCL_DATA_DIR")
    data = Path(data_dir).expanduser().resolve() if data_dir else PROJECT_ROOT / "data"
    return Paths(root=PROJECT_ROOT, data=data)


def kaggle_configured() -> bool:
    """True when Kaggle API credentials are available (env vars or ``~/.kaggle/kaggle.json``)."""
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    return (Path.home() / ".kaggle" / "kaggle.json").is_file()
