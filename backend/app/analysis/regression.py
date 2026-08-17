"""Linear and continuous piecewise-linear ("broken-stick") regression."""
from __future__ import annotations

import numpy as np
from scipy import stats


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


def _hinge_fit(x: np.ndarray, y: np.ndarray, breakpoints: list[float]):
    """
    Continuous piecewise-linear OLS fit: y = b0 + b1*x + sum_j c_j*relu(x - bp_j).
    The relu basis forces neighboring segments to meet exactly at each bp_j.
    Returns (coef, ssr); coef is None (ssr = 1e18) on a degenerate fit.
    """
    cols = [np.ones_like(x), x] + [np.clip(x - bp, 0, None) for bp in breakpoints]
    X = np.column_stack(cols)
    try:
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    except Exception:
        return None, 1e18
    pred = X @ coef
    if not np.all(np.isfinite(pred)):
        return None, 1e18
    return coef, float(np.sum((y - pred) ** 2))


def _hinge_segments(x: np.ndarray, y: np.ndarray, idx_bounds: list[int],
                     breakpoints: list[float], coef: np.ndarray) -> list[dict]:
    """Derive per-segment {slope, intercept, r2, xr} dicts from continuous hinge coefficients."""
    cols = [np.ones_like(x), x] + [np.clip(x - bp, 0, None) for bp in breakpoints]
    pred = np.column_stack(cols) @ coef
    slope, intercept = coef[1], coef[0]
    segs = []
    for i in range(len(idx_bounds) - 1):
        if i > 0:
            c, bp = coef[i + 1], breakpoints[i - 1]
            slope = slope + c
            intercept = intercept - c * bp
        sl = slice(idx_bounds[i], idx_bounds[i + 1])
        xi, yi, pi = x[sl], y[sl], pred[sl]
        x0 = breakpoints[i - 1] if i > 0 else x[0]
        x1 = breakpoints[i] if i < len(breakpoints) else x[-1]
        if xi.size >= 2 and np.ptp(yi) > 0:
            ss_res = float(np.sum((yi - pi) ** 2))
            ss_tot = float(np.sum((yi - yi.mean()) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        else:
            r2 = float("nan")
        segs.append(dict(slope=float(slope), intercept=float(intercept),
                          r2=r2, xr=(float(x0), float(x1))))
    return segs


def piecewise_fit(x_in, y_in, n_seg: int) -> dict:
    """
    Continuous ("broken-stick") piecewise linear fit via exhaustive breakpoint
    search. Every segment is guaranteed >= 2 points; degenerate inputs fall
    back gracefully to a single-segment fit. Because segments share one
    continuous model, neighboring fit lines always meet exactly at each
    breakpoint (no jump).
    Returns {"segments": [{slope, intercept, r2, xr=(x0, x1)}, ...], "breakpoints": [x, ...]}.
    """
    x = np.asarray(x_in, float)
    y = np.asarray(y_in, float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 2:
        return {"segments": [], "breakpoints": []}
    ix = np.argsort(x)
    x, y = x[ix], y[ix]

    def _single() -> dict:
        f = lin_reg(x, y)
        if f:
            f["xr"] = (float(x[0]), float(x[-1]))
            return {"segments": [f], "breakpoints": []}
        return {"segments": [], "breakpoints": []}

    # Need ≥ 2 points per segment
    if n_seg <= 1 or n < n_seg * 2:
        return _single()

    # Defaults are evenly-spaced so the search always has a valid fallback
    # partition even when the loop below is empty (n exactly equals n_seg * 2).
    if n_seg == 2:
        best, bk = 1e18, n // 2
        # k in [2, n-2] (inclusive) — each segment gets ≥ 2 points
        for k in range(2, n - 1):
            _, ssr = _hinge_fit(x, y, [x[k]])
            if ssr < best:
                best, bk = ssr, k
        bps_idx = [bk]

    elif n_seg == 3:
        best, bk1, bk2 = 1e18, n // 3, 2 * n // 3
        for k1 in range(2, n - 3):
            for k2 in range(k1 + 2, n - 1):
                _, ssr = _hinge_fit(x, y, [x[k1], x[k2]])
                if ssr < best:
                    best, bk1, bk2 = ssr, k1, k2
        bps_idx = [bk1, bk2]

    elif n_seg == 4:
        best, b1i, b2i, b3i = 1e18, n // 4, n // 2, 3 * n // 4
        for k1 in range(2, n - 5):
            for k2 in range(k1 + 2, n - 3):
                for k3 in range(k2 + 2, n - 1):
                    _, ssr = _hinge_fit(x, y, [x[k1], x[k2], x[k3]])
                    if ssr < best:
                        best, b1i, b2i, b3i = ssr, k1, k2, k3
        bps_idx = [b1i, b2i, b3i]

    else:
        # n_seg > 4: evenly-spaced breakpoints, clamped to ≥ 2 pts per segment
        bps_idx = sorted(set(
            max(2 * i, min(n - 2 * (n_seg - i), int(n * i / n_seg)))
            for i in range(1, n_seg)
        ))

    breakpoints = [float(x[k]) for k in bps_idx]
    coef, _ = _hinge_fit(x, y, breakpoints)
    if coef is None:
        return _single()
    idx_bounds = [0] + bps_idx + [n]
    segs = _hinge_segments(x, y, idx_bounds, breakpoints, coef)
    return {"segments": segs, "breakpoints": breakpoints}
