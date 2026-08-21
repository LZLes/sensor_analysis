"""Generic numeric/signal-processing helpers shared across modes."""

import numpy as np
import pandas as pd
from scipy import stats


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def smooth_signal(arr: np.ndarray, method: str, window: int, polyorder: int = 2) -> np.ndarray:
    """Optional smoothing for a 1-D signal. Returns arr unchanged if method == 'None'."""
    if method == "None" or arr.size == 0:
        return arr
    window = max(3, int(window) | 1)   # coerce to odd, >= 3
    if method == "Moving average":
        return pd.Series(arr).rolling(window, center=True, min_periods=1).mean().to_numpy()
    if method == "Savitzky-Golay":
        from scipy.signal import savgol_filter
        window = min(window, arr.size if arr.size % 2 else arr.size - 1)
        if window < 3:
            return arr
        po = min(int(polyorder), window - 1)
        return savgol_filter(arr, window_length=window, polyorder=po, mode="interp")
    return arr


def lin_reg(x: np.ndarray, y: np.ndarray) -> dict | None:
    """OLS linear regression → {slope, intercept, r2} or None."""
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 2:
        return None
    xm, ym = x[m], y[m]
    if np.ptp(xm) == 0:          # all x identical → undefined slope
        return None
    try:
        s, b, r, *_ = stats.linregress(xm, ym)
        if not (np.isfinite(s) and np.isfinite(r)):
            return None
        return dict(slope=float(s), intercept=float(b), r2=float(r ** 2))
    except Exception:
        return None


def _is_float(v: str) -> bool:
    try:
        float(str(v).strip().replace(",", "."))
        return True
    except ValueError:
        return False


def _eff_t_start(row) -> float | None:
    """Effective t_start: t_end − avg_duration if set (> 0), otherwise t_start."""
    ad = row.get("avg_duration")
    if pd.notna(ad) and float(ad) > 0 and pd.notna(row.get("t_end")):
        return float(row["t_end"]) - float(ad)
    v = row.get("t_start")
    return float(v) if pd.notna(v) else None
