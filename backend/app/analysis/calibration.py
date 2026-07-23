"""Amperometry/solid-state calibration-table helpers (windowing, effective concentration)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def eff_t_start(row) -> float | None:
    """Effective t_start: t_end − avg_duration if set (> 0), otherwise t_start."""
    ad = row.get("avg_duration")
    if pd.notna(ad) and float(ad) > 0 and pd.notna(row.get("t_end")):
        return float(row["t_end"]) - float(ad)
    v = row.get("t_start")
    return float(v) if pd.notna(v) else None


def apply_effective_concentration(cpdf: pd.DataFrame, initial_volume: float) -> pd.DataFrame:
    """Returns a copy of a calibration table with Concentration derived from
    cumulative, dilution-corrected Spike Vol / Stock Conc additions (if any
    are filled in), and t_start derived from avg_duration (if set). A no-op
    copy when neither is used, so it's safe to call unconditionally."""
    calc_df = cpdf.copy()
    if calc_df[["Spike Vol", "Stock Conc"]].notna().any().any():
        vol, mass, eff = float(initial_volume), 0.0, []
        for _, row in calc_df.iterrows():
            sv = row.get("Spike Vol", 0.0)
            sc = row.get("Stock Conc", 0.0)
            sv = 0.0 if pd.isna(sv) else float(sv)
            sc = 0.0 if pd.isna(sc) else float(sc)
            vol += sv
            mass += sv * sc
            eff.append(mass / vol if vol > 0 else np.nan)
        calc_df["Concentration"] = eff
    for ti, trow in calc_df.iterrows():
        if pd.notna(trow.get("avg_duration")) and pd.notna(trow.get("t_end")):
            calc_df.at[ti, "t_start"] = eff_t_start(trow)
    return calc_df
