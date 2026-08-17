"""Small shared helpers used across the analysis package."""
from __future__ import annotations

import numpy as np
import pandas as pd

# Shared colour palette (up to 8 channels + 1 average) — ported verbatim so
# frontend/backend renders stay visually consistent with the original app.
PAL = [
    "#4c96d7", "#ff9230", "#2ecc71", "#e05c5c",
    "#b39ddb", "#f0a050", "#f48fb1", "#6d8ea0",
]
AVG_COLOR = "#555555"   # dark charcoal for channel-average — readable on both white and dark backgrounds


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def fmt(val, p: int = 4) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:.{p}g}"


def _is_float(v: str) -> bool:
    try:
        float(str(v).strip().replace(",", "."))
        return True
    except ValueError:
        return False


def amp_label(filename: str, ch_name: str, multi: bool) -> str:
    """Composite (file, channel) label — bare channel name when only one file is loaded."""
    return f"{filename} · {ch_name}" if multi else ch_name
