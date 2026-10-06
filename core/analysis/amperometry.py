"""Amperometry maths: segmented-linear (hinge) fits, the effective-concentration
dilution calculator, calibration-table presets, sample data and the
calibration-curve export figure. Used by modes/amperometry.py (Streamlit)
and web_app/api/amperometry.py."""

import io
import os

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from core.calibration_table import _baseline_keep_mask
from core.constants import _SAMPLE_DATA_DIR, AVG_COLOR, PAL
from core.numeric import _eff_t_start, lin_reg
from core.plotting import _MINIMAL_RC, _ORIGIN_RC, _apply_spine_style


def _cpdf_from_autodetect_windows(windows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """Turns generic (label, t_start, t_end) triples from Auto-detect into an
    Amperometry-schema calibration table. Concentration is left blank —
    times are recoverable from the trace, concentrations aren't."""
    return pd.DataFrame({
        "Label":         [w[0] for w in windows],
        "Concentration": [0.0] * len(windows),
        "Spike Vol":     [np.nan] * len(windows),
        "Stock Conc":    [np.nan] * len(windows),
        "t_start":       [w[1] for w in windows],
        "t_end":         [w[2] for w in windows],
        "avg_duration":  [np.nan] * len(windows),
        "Baseline":      [w[0] == "Baseline" for w in windows],
    })


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


def _apply_effective_concentration(cpdf: pd.DataFrame, initial_volume: float) -> pd.DataFrame:
    """Returns a copy of a calibration table with Concentration derived from
    cumulative, dilution-corrected Spike Vol / Stock Conc additions (if any
    are filled in), and t_start derived from avg_duration (if set). A no-op
    copy when neither is used, so it's safe to call unconditionally."""
    _calc_df = cpdf.copy()
    if _calc_df[["Spike Vol", "Stock Conc"]].notna().any().any():
        _vol, _mass, _eff = float(initial_volume), 0.0, []
        for _, _row in _calc_df.iterrows():
            _sv = _row.get("Spike Vol", 0.0)
            _sc = _row.get("Stock Conc", 0.0)
            _sv = 0.0 if pd.isna(_sv) else float(_sv)
            _sc = 0.0 if pd.isna(_sc) else float(_sc)
            _vol  += _sv
            _mass += _sv * _sc
            _eff.append(_mass / _vol if _vol > 0 else np.nan)
        _calc_df["Concentration"] = _eff
    for _ti, _trow in _calc_df.iterrows():
        if pd.notna(_trow.get("avg_duration")) and pd.notna(_trow.get("t_end")):
            _calc_df.at[_ti, "t_start"] = _eff_t_start(_trow)
    return _calc_df


def render_cal_png(res_map: dict, ft: str, ns: int,
                   conc_unit: str, cur_unit: str,
                   dpi: int = 150, fmt: str = "png",
                   figsize: tuple | None = None, style: str = "default") -> bytes:
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11
    _lgfs = 7 if style == "minimal" else 9
    _afs = 6.5 if style == "minimal" else 7.5
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (8, 6))
        _annot_blocks = []
        for j, (ch_name, res) in enumerate(res_map.items()):
            col  = AVG_COLOR if res.get("is_average") else PAL[j % len(PAL)]
            # Same blank-exclusion as the in-app Plotly chart, kept in sync
            # via the shared _baseline_keep_mask helper.
            _keep = _baseline_keep_mask(res.get("baselines", [False] * len(res["concs"])))
            x    = np.asarray(res["concs"], dtype=float)[_keep]
            y    = np.array(res["delta_i"], float)[_keep]
            errs = [float(s) if (s and not np.isnan(s)) else 0.0
                    for s in np.asarray(res["sigs"], dtype=float)[_keep]]
            marker = "D" if res.get("is_average") else "o"
            _yerr  = errs if res.get("is_average") else None
            ax.errorbar(x, y, yerr=_yerr, fmt=marker, color=col, label=ch_name,
                        capsize=4, markersize=7, linewidth=1.4, elinewidth=1.2)
            _pf = piecewise_fit(x, y, int(ns) if ft == "Segmented Linear" else 1)
            segs, breakpoints = _pf["segments"], _pf["breakpoints"]
            sigma_bl = float(res.get("sigma_bl", np.nan))
            _ch_lines = [ch_name + ":"]
            for k, seg in enumerate(segs):
                xp = np.linspace(seg["xr"][0], seg["xr"][1], 300)
                yp = seg["slope"] * xp + seg["intercept"]
                ls = (0, (5, 2)) if res.get("is_average") else "--"
                ax.plot(xp, yp, linestyle=ls, color=col, linewidth=2)
                s, b, r2 = seg["slope"], seg["intercept"], seg["r2"]
                _pfx = f"  seg {k + 1} " if len(segs) > 1 else "  "
                _sign = "+" if b >= 0 else "−"
                _ch_lines.append(f"{_pfx}y = {s:.3g}x {_sign} {abs(b):.3g}   R² = {r2:.4f}")
                if np.isfinite(sigma_bl) and s != 0:
                    # y = a + b*x; blank response in ΔI units is 0, so solve
                    # the fitted line for x at y = 3.3*sigma (10*sigma for LOQ)
                    # instead of assuming the intercept a is 0.
                    lod = (3.3  * abs(sigma_bl) - b) / s
                    loq = (10.0 * abs(sigma_bl) - b) / s
                    _ch_lines.append(
                        f"{_pfx}Sens = {s:.3g} {cur_unit}/{conc_unit}"
                        f"   LOD = {lod:.3g}   LOQ = {loq:.3g} {conc_unit}"
                    )
            for bp in breakpoints:
                ax.axvline(bp, linestyle=":", color=col, linewidth=1.2)
                ax.annotate(f"{bp:.3g} {conc_unit}", xy=(bp, 1), xycoords=("data", "axes fraction"),
                            xytext=(2, -2), textcoords="offset points",
                            fontsize=_afs, color=col, rotation=90, va="top", ha="left")
            _annot_blocks.append("\n".join(_ch_lines))
        ax.set_xlabel(f"Concentration ({conc_unit})", fontsize=_lfs)
        ax.set_ylabel(f"ΔI ({cur_unit})", fontsize=_lfs)
        ax.legend(fontsize=_lgfs, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        if _annot_blocks:
            # Place below the axes after tight_layout; bbox_inches="tight" captures it
            ax.text(
                0.5, -0.22, "\n\n".join(_annot_blocks),
                transform=ax.transAxes, fontsize=_afs,
                va="top", ha="center", family="monospace",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                          alpha=0.88, edgecolor="#cccccc", linewidth=0.8),
            )
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


_AMP_PRESETS = {
    "Serial spike: 25 mM ×2, 50 mM ×2, 100 mM ×4": {
        "increments": [25, 25, 50, 50, 100, 100, 100, 100],
        "start": 600.0,
        "interval": 300.0,
    },
}


def _spike_vol_for_targets(targets: list[float], stock_conc: float,
                            initial_volume: float) -> list[float]:
    """Inverse of the serial-dilution mass balance in
    _apply_effective_concentration: given a (constant) stock concentration
    and initial vessel volume, solves for the Spike Vol at each step that
    hits the corresponding target cumulative concentration exactly.
    Requires stock_conc > every target (a stock can't be weaker than the
    concentration it's diluting into) — raises ValueError otherwise."""
    if stock_conc <= max(targets):
        raise ValueError("Stock Conc must be greater than every target concentration.")
    vol, mass, spikes = initial_volume, 0.0, []
    for c in targets:
        sv = (c * vol - mass) / (stock_conc - c)
        spikes.append(sv)
        vol  += sv
        mass += sv * stock_conc
    return spikes


def _preset_cpdf_amp(increments: list[float], start: float, interval: float | list[float],
                      include_blank: bool, stock_conc: float, initial_volume: float,
                      avg_window: float) -> pd.DataFrame:
    """Builds an Amperometry calibration table from a serial-spike protocol:
    cumulative concentration steps, the first starting at `start`, optionally
    preceded by a Blank/baseline row spanning 0 → start. `interval` is either
    a single duration applied to every step, or a list with one duration per
    step for unevenly-spaced protocols.

    Stock Conc is optional: pass 0 to skip the Spike Vol back-solve entirely
    and just use `increments` as the Concentration column directly (the
    common case when you already know your target concentrations and don't
    need the dilution math). Pass a value greater than every cumulative
    target to also back-solve Spike Vol from stock_conc / initial_volume so
    it reproduces the same cumulative Concentration via
    _apply_effective_concentration (clicking Preview afterward is then a
    no-op). avg_window fills Avg window (s) on every row, so averaging uses
    the tail of each interval rather than the whole thing."""
    intervals = [float(interval)] * len(increments) if np.isscalar(interval) else list(interval)
    if len(intervals) != len(increments):
        raise ValueError(
            f"interval count ({len(intervals)}) must be 1 (applied to every step) or "
            f"match the number of increments ({len(increments)})"
        )

    labels, concs, t_starts, t_ends, baselines = [], [], [], [], []
    if include_blank:
        labels.append("Blank"); concs.append(0.0)
        t_starts.append(0.0); t_ends.append(start); baselines.append(True)
    cum, t = 0.0, start
    for i, (inc, dur) in enumerate(zip(increments, intervals), start=1):
        cum += inc
        labels.append(f"Step {i}"); concs.append(cum)
        t_starts.append(t); t_ends.append(t + dur); baselines.append(False)
        t += dur
    n = len(labels)

    spike_vols  = [np.nan] * n
    stock_concs = [np.nan] * n
    if stock_conc > 0:
        _spiked_targets = concs[1:] if include_blank else concs
        _spikes = _spike_vol_for_targets(_spiked_targets, stock_conc, initial_volume)
        for i, sv in enumerate(_spikes, start=(1 if include_blank else 0)):
            spike_vols[i]  = sv
            stock_concs[i] = stock_conc

    return pd.DataFrame({
        "Label":         labels,
        "Concentration": concs,
        "Spike Vol":     spike_vols,
        "Stock Conc":    stock_concs,
        "t_start":       t_starts,
        "t_end":         t_ends,
        "avg_duration":  [avg_window] * n,
        "Baseline":      baselines,
    })


_SAMPLE_STEPS = [
    ("Blank",  0.0,   0.0,  50.0, True),
    ("Step 1", 0.1,  70.0, 110.0, False),
    ("Step 2", 0.5, 130.0, 170.0, False),
    ("Step 3", 1.0, 190.0, 230.0, False),
    ("Step 4", 2.0, 250.0, 290.0, False),
]


_SAMPLE_FILES = ["sensor_run_A.csv", "sensor_run_B.csv"]


def _sample_cpdf() -> pd.DataFrame:
    return pd.DataFrame({
        "Label":         [s[0] for s in _SAMPLE_STEPS],
        "Concentration": [s[1] for s in _SAMPLE_STEPS],
        "Spike Vol":     [np.nan] * len(_SAMPLE_STEPS),
        "Stock Conc":    [np.nan] * len(_SAMPLE_STEPS),
        "t_start":       [s[2] for s in _SAMPLE_STEPS],
        "t_end":         [s[3] for s in _SAMPLE_STEPS],
        "avg_duration":  [np.nan] * len(_SAMPLE_STEPS),
        "Baseline":      [s[4] for s in _SAMPLE_STEPS],
    })


def _load_sample_data() -> list[dict] | None:
    """Reads the bundled sample_data/*.csv files and returns fully-configured
    amp_files entries (channels mapped, calibration table pre-filled), or
    None if the files aren't present (e.g. a stripped-down deployment)."""
    _files = []
    for _fn in _SAMPLE_FILES:
        _path = os.path.join(_SAMPLE_DATA_DIR, _fn)
        if not os.path.isfile(_path):
            return None
        _df = pd.read_csv(_path)
        _channels = [
            {"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"},
            {"name": "Channel B", "tc": "Time (s)", "ic": "Channel B (uA)"},
        ]
        _files.append({
            "filename": _fn, "df": _df, "channels": _channels,
            "cpdf": _sample_cpdf(),
        })
    return _files
