"""Amperometry- and Solid-State-schema calibration-table builders (also used
by core.persistence to round-trip amp_files/solid_files, so this lives in
core rather than modes.amperometry/modes.solid_state to avoid
modes -> core -> modes import cycles)."""

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


def _baseline_keep_mask(baselines) -> list[bool]:
    """True for rows to KEEP (i.e. NOT baseline/blank) when excluding the
    baseline point from a plotted curve or fit. NaN is treated as
    not-baseline (False), same as everywhere else Baseline is coerced to
    bool — used by both the interactive and PNG-export renderers so they
    can't diverge on how a NaN Baseline cell is handled."""
    return [not (bool(b) if pd.notna(b) else False) for b in baselines]


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


def _default_solid_cpdf() -> pd.DataFrame:
    """Starter calibration table for Solid-State (potentiometric) sensors.
    No Baseline/Spike Vol/Stock Conc — Nernstian fits use raw potential
    directly, and there's no dilution-calculator support here. Reading_mV
    is a nullable direct-entry column: fill it in to skip windowed
    averaging from an imported trace entirely."""
    return pd.DataFrame({
        "Label":         ["Std 1", "Std 2", "Std 3", "Std 4"],
        "Concentration": [0.1, 1.0, 10.0, 100.0],
        "t_start":       [0.0, 120.0, 240.0, 360.0],
        "t_end":         [60.0, 180.0, 300.0, 420.0],
        "avg_duration":  [np.nan, np.nan, np.nan, np.nan],
        "Reading_mV":    [np.nan, np.nan, np.nan, np.nan],
    })


def _solid_cpdf_from_records(records: list[dict] | None) -> pd.DataFrame:
    """Solid-State equivalent of _cpdf_from_records — kept separate (rather
    than reusing the Amperometry version) since the two schemas differ:
    reusing _cpdf_from_records on Solid-State records would spuriously add
    Amperometry-only "Spike Vol"/"Stock Conc"/"Baseline" columns and, for an
    empty table, seed it with Amperometry's default rows instead of
    Solid-State's."""
    if not records:
        return _default_solid_cpdf()
    _cp = pd.DataFrame(records)
    for _col in ["Concentration", "t_start", "t_end", "avg_duration", "Reading_mV"]:
        _cp[_col] = pd.to_numeric(_cp[_col], errors="coerce") if _col in _cp.columns else np.nan
    if "Label" not in _cp.columns:
        _cp["Label"] = [f"Row {i + 1}" for i in range(len(_cp))]
    return _cp
