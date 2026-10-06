"""Cyclic voltammetry maths: peak detection. Used by modes/cyclic_voltammetry.py
(Streamlit) and web_app/api/cyclic_voltammetry.py."""

import numpy as np


def find_cv_peaks(voltage: np.ndarray, current: np.ndarray,
                  prominence: float, distance: int,
                  width: int | None = None,
                  height: float | None = None) -> dict:
    """
    Detect anodic (local maxima) and cathodic (local minima) peaks in a CV trace.
    prominence : min height relative to surrounding baseline (0 = no filter,
                 same convention as width/height below)
    distance   : min data-points between peaks
    width      : min peak width in data-points (None = no filter)
    height     : min absolute |Ip| (applied to both anodic and cathodic; None = no filter)
    Returns {anodic: [{Ep, Ip}, …], cathodic: [{Ep, Ip}, …]}.
    """
    import scipy.signal  # type: ignore[import-untyped]
    mask = ~(np.isnan(voltage) | np.isnan(current))
    v, i = voltage[mask], current[mask]
    if len(v) < 5:
        return {"anodic": [], "cathodic": []}
    _kw: dict = dict(distance=max(1, distance))
    if prominence > 0:                     _kw["prominence"] = prominence
    if width  is not None and width  > 0:  _kw["width"]  = width
    if height is not None and height > 0:  _kw["height"] = height
    anodic_idx,   _ = scipy.signal.find_peaks(i,  **_kw)
    cathodic_idx, _ = scipy.signal.find_peaks(-i, **_kw)
    return {
        "anodic":   [{"Ep": float(v[k]), "Ip": float(i[k])} for k in anodic_idx],
        "cathodic": [{"Ep": float(v[k]), "Ip": float(i[k])} for k in cathodic_idx],
    }
