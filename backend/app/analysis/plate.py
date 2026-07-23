"""96-well microplate (Assay) parsing helpers."""
from __future__ import annotations

import re as _re

import numpy as np
import pandas as pd

PLATE_ROWS = list("ABCDEFGH")


def well_rc(well: str) -> tuple[int, int] | None:
    """'A1' → (0, 0), 'H12' → (7, 11). None if invalid."""
    w = well.strip().upper()
    if not w or w[0] not in PLATE_ROWS:
        return None
    try:
        c = int(w[1:]) - 1
    except ValueError:
        return None
    return (PLATE_ROWS.index(w[0]), c) if 0 <= c < 12 else None


def plate_get(plate_df: pd.DataFrame | None, well: str) -> float:
    rc = well_rc(well)
    if rc is None or plate_df is None:
        return np.nan
    try:
        return float(plate_df.iat[rc[0], rc[1]])
    except Exception:
        return np.nan


def parse_plate_csv(raw: str) -> pd.DataFrame:
    """
    Parse a microplate reader export into an 8×12 DataFrame (index A–H, cols 1–12).
    Handles TECAN/Synergy/generic grid formats (tab, comma, semicolon delimited).
    """
    row_re = _re.compile(r'^\s*([A-Ha-h])(?:[,;\t]|\s)')
    grid: dict[str, list[float]] = {}
    for line in raw.splitlines():
        m = row_re.match(line)
        if not m:
            continue
        letter = m.group(1).upper()
        parts = _re.split(r'[,;\t]+', line.strip())
        if len(parts) < 2:
            parts = line.strip().split()
        nums: list[float] = []
        for p in parts[1:]:
            try:
                nums.append(float(p.strip().replace(",", ".")))
            except ValueError:
                continue
        if nums:
            grid[letter] = nums[:12]
    if not grid:
        raise ValueError(
            "No plate rows found — expected rows labeled A–H. "
            "Check the file has a standard grid layout."
        )
    data = {}
    for r in PLATE_ROWS:
        row_vals = (grid.get(r, []) + [np.nan] * 12)[:12]
        data[r] = row_vals
    df = pd.DataFrame(data, index=range(1, 13)).T
    df.index = pd.Index(PLATE_ROWS, name="Row")
    df.columns = pd.Index(range(1, 13), name="Col")
    return df
