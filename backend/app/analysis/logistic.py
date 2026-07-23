"""4-parameter logistic (4PL) fit, used by the Assay standard curve."""
from __future__ import annotations

import numpy as np


def fit_4pl(x: np.ndarray, y: np.ndarray) -> dict | None:
    """4-parameter logistic: y = d + (a − d) / (1 + (x/c)^b)."""
    from scipy.optimize import curve_fit

    def _model(xv, a, b, c, d):
        return d + (a - d) / (1.0 + (np.asarray(xv) / c) ** b)

    xpos = x[x > 0]
    c0 = float(np.median(xpos)) if xpos.size else 1.0
    try:
        popt, _ = curve_fit(
            _model, x, y,
            p0=[float(y.min()), 1.0, c0, float(y.max())],
            maxfev=10000,
            bounds=([-np.inf, 0.01, 1e-12, -np.inf],
                    [np.inf, 10.0, np.inf, np.inf]),
        )
        yp = _model(x, *popt)
        ss_res = float(np.sum((y - yp) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return dict(type="4pl", a=popt[0], b=popt[1], c=popt[2], d=popt[3], r2=r2)
    except Exception:
        return None


def inv_4pl(y_val: float, p: dict) -> float:
    a, b, c, d = p["a"], p["b"], p["c"], p["d"]
    try:
        ratio = (a - d) / (float(y_val) - d)
        return float(c * (ratio - 1.0) ** (1.0 / b)) if ratio > 0 else np.nan
    except Exception:
        return np.nan
