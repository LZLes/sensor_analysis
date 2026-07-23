"""
Potentiometric (solid-state, e.g. ISE/ISFET) calibration fitting.

Unlike Amperometry's linear ΔI-vs-concentration model, solid-state sensors
respond with potential E (mV) linear in log10(concentration) — the Nernst
equation. Sensitivity is reported in mV/decade, and the limit of detection
is conventionally defined as the concentration where two INDEPENDENTLY
fitted regimes intersect:
  - a "Nernstian" high-concentration regime (near-ideal slope)
  - a flattened low-concentration regime (the sensor saturates/plateaus)

This is a genuinely different LOD definition from Amperometry's
3·sigma_blank/slope, and it is NOT the same thing as `piecewise_fit`'s
continuous ("broken-stick") 2-segment fit: piecewise_fit forces its two
segments to meet exactly at a breakpoint that must land on one of the
input x-values, which is a display-friendly continuous curve but not what
"LOD" means in the ISE literature. There, the two regimes are fit
independently and the LOD is wherever those two (generally non-touching)
lines *would* cross, which is usually between two standards, not at one.
"""
from __future__ import annotations

import numpy as np

from .regression import lin_reg

# Physical constants (SI units)
_GAS_CONSTANT_R = 8.314462618       # J / (mol*K)
_FARADAY_F = 96485.33212            # C / mol


def nernst_ideal_slope_mv(temp_c: float = 25.0, z: int = 1) -> float:
    """
    Ideal Nernstian slope in mV/decade: (R*T*ln(10)) / (z*F), converted to mV.
    z is the ion charge (e.g. 1 for Na+/K+/Cl-, 2 for Ca2+/Mg2+). Temperature
    matters — don't hardcode 59 mV, since lab temperature varies.
    """
    temp_k = temp_c + 273.15
    slope_v = (_GAS_CONSTANT_R * temp_k * np.log(10)) / (abs(z) * _FARADAY_F)
    return float(slope_v * 1000.0)


def nernstian_lod_fit(log_conc: np.ndarray, potential_mv: np.ndarray) -> dict:
    """
    Fit two independent linear regressions — a low-concentration
    ("flattened") regime and a high-concentration ("Nernstian") regime —
    choosing the split point by exhaustive search to minimize total SSR
    across both segments (each segment requires >= 2 points). The
    reported LOD is where the two independently-fitted lines intersect,
    which generally does not coincide with any input data point.

    Inputs are assumed pre-validated: log_conc must not contain -inf/NaN
    (i.e. the caller has already rejected Concentration <= 0 rows, since
    log10(0) is undefined and log10(negative) is complex).

    Returns:
        {
          "low_segment":       {slope, intercept, r2} | None,
          "nernstian_segment": {slope, intercept, r2} | None,
          "lod_log10": float,   # NaN if no valid intersection
          "lod_conc":  float,   # 10**lod_log10, NaN if lod_log10 is NaN
          "split_index": int | None,   # index into the sorted, filtered input
        }
    """
    x = np.asarray(log_conc, dtype=float)
    y = np.asarray(potential_mv, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)

    _empty = {
        "low_segment": None, "nernstian_segment": None,
        "lod_log10": float("nan"), "lod_conc": float("nan"),
        "split_index": None,
    }
    if n < 4:
        # Not enough points to fit two independent >=2-point segments.
        # Fall back to reporting a single overall fit as the "Nernstian"
        # segment so callers still get a usable slope/intercept/R².
        single = lin_reg(x, y)
        return {**_empty, "nernstian_segment": single}

    order = np.argsort(x)
    x, y = x[order], y[order]

    best_ssr = float("inf")
    best_k = None
    best_low = None
    best_high = None
    for k in range(2, n - 1):   # both sides get >= 2 points
        low_fit = lin_reg(x[:k], y[:k])
        high_fit = lin_reg(x[k:], y[k:])
        if low_fit is None or high_fit is None:
            continue
        pred_low = low_fit["slope"] * x[:k] + low_fit["intercept"]
        pred_high = high_fit["slope"] * x[k:] + high_fit["intercept"]
        ssr = float(np.sum((y[:k] - pred_low) ** 2) + np.sum((y[k:] - pred_high) ** 2))
        if ssr < best_ssr:
            best_ssr, best_k = ssr, k
            best_low, best_high = low_fit, high_fit

    if best_low is None or best_high is None:
        single = lin_reg(x, y)
        return {**_empty, "nernstian_segment": single}

    slope_diff = best_high["slope"] - best_low["slope"]
    if slope_diff == 0:
        lod_log10 = float("nan")
    else:
        lod_log10 = (best_low["intercept"] - best_high["intercept"]) / slope_diff

    lod_conc = float(10.0 ** lod_log10) if np.isfinite(lod_log10) else float("nan")

    return {
        "low_segment": best_low,
        "nernstian_segment": best_high,
        "lod_log10": float(lod_log10) if np.isfinite(lod_log10) else float("nan"),
        "lod_conc": lod_conc,
        "split_index": int(best_k),
    }
