"""Signal smoothing."""
from __future__ import annotations

import numpy as np
import pandas as pd


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
