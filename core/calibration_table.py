"""Amperometry-schema calibration-table builders (also used by core.persistence
to round-trip amp_files, so this lives in core rather than modes.amperometry to
avoid modes -> core -> modes import cycles)."""

import numpy as np
import pandas as pd


def _default_cpdf() -> pd.DataFrame:
    """A fresh starter calibration table — used to seed each newly-imported
    file's own table (calibration tables are per-file, not shared)."""
    return pd.DataFrame({
        "Label":         ["Blank", "Step 1", "Step 2", "Step 3"],
        "Concentration": [0.0, 0.1, 0.5, 1.0],
        "Spike Vol":     [np.nan, np.nan, np.nan, np.nan],
        "Stock Conc":    [np.nan, np.nan, np.nan, np.nan],
        "t_start":       [0.0, 120.0, 300.0, 480.0],
        "t_end":         [60.0, 180.0, 360.0, 540.0],
        "avg_duration":  [np.nan, np.nan, np.nan, np.nan],
        "Baseline":      [True, False, False, False],
    })


def _cpdf_from_records(records: list[dict] | None) -> pd.DataFrame:
    """Build a well-typed calibration DataFrame from serialized records
    (localStorage / JSON import / Drive), or a fresh default table if empty."""
    if not records:
        return _default_cpdf()
    _cp = pd.DataFrame(records)
    for _col in ["Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration"]:
        _cp[_col] = pd.to_numeric(_cp[_col], errors="coerce") if _col in _cp.columns else np.nan
    _cp["Baseline"] = (
        _cp["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)
        if "Baseline" in _cp.columns else False
    )
    if "Label" not in _cp.columns:
        _cp["Label"] = [f"Row {i + 1}" for i in range(len(_cp))]
    return _cp
