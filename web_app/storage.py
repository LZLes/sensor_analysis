"""Where the web app keeps files between runs: the single-instance state
file, autosaves and Assay layout templates.

~/Library/Application Support/Sensor Calibration Studio on macOS,
~/.sensor-calibration-studio elsewhere. SCS_DATA_DIR overrides it (the
tests point it at a temporary folder).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("SCS_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "Sensor Calibration Studio"
    else:
        path = Path.home() / ".sensor-calibration-studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def subdir(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path: Path, payload) -> None:
    """Write via a temp file + rename, so a crash never leaves half a file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
