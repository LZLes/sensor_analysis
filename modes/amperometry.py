"""Amperometry mode: linear/segmented-linear ΔI vs Concentration calibration,
with Baseline subtraction and an effective-concentration dilution calculator
(unlike Solid-State)."""

import io
import os

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ai_insights import _render_ai_insights_section
from core.calibration_table import _default_cpdf, _baseline_keep_mask
from core.constants import PAL, AVG_COLOR, _MIME, _SAMPLE_DATA_DIR, _plot_theme, fmt
from core.numeric import to_num, smooth_signal, lin_reg, _eff_t_start
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style
from core.shared_tabs import (
    _amp_label, _render_autodetect_expander, _render_import_tab,
    _render_timeseries_tab, render_ts_png,
)

SS = st.session_state


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
                    lod = 3.0 * abs(sigma_bl) / abs(s)
                    loq = 10.0 * abs(sigma_bl) / abs(s)
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


def _seed_cpdf_for_new_file() -> pd.DataFrame:
    """Starter calibration table for a newly-imported file: reuses the
    table from an imported legacy (pre-per-file) session if one was just
    restored, otherwise a fresh default."""
    _tmpl = SS.get("_legacy_cpdf_template")
    return _tmpl.copy() if _tmpl is not None else _default_cpdf()


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


def render() -> None:
    T1, T2, T3, T4 = st.tabs([
        "① Import & Configure", "② Time Series", "③ Calibration Curve", "④ Export",
    ])
    
    
    # ═════════════════════════════════════════════════════════════════════════════
    # TAB 1 · Import & Configure
    # ═════════════════════════════════════════════════════════════════════════════
    with T1:
        _render_import_tab(
            files_key="amp_files",
            signal_col_label="Current",
            unit_key="cur_unit",
            active_file_key="cal_active_file",
            seed_cpdf_fn=_seed_cpdf_for_new_file,
            sample_loader_fn=_load_sample_data,
            sample_caption=(
                "Two synthetic amperometric runs (2 channels each) with a "
                "ready-made calibration table — a quick way to see the whole "
                "workflow before importing your own files."
            ),
            sample_button_help=(
                "Loads two bundled example runs (2 channels each, with a "
                "pre-filled calibration table) so you can try the app immediately "
                "without your own data."
            ),
            sample_loaded_msg=(
                "Sample data loaded — 2 files, 2 channels each, with a "
                "matching calibration table already filled in. Head to the "
                "**Time Series** or **Calibration Curve** tab to explore."
            ),
            sample_conc_unit="mM",
            sample_signal_unit="\u00b5A",
            set_legacy_alias=True,
        )
    
    
    # ═════════════════════════════════════════════════════════════════════════════
    # TAB 2 · Time Series
    # ═════════════════════════════════════════════════════════════════════════════
    with T2:
        _render_timeseries_tab(files_key="amp_files", unit_key="cur_unit", signal_axis_label="Current")
    
    
    # ═════════════════════════════════════════════════════════════════════════════
    # TAB 3 · Calibration Curve
    # ═════════════════════════════════════════════════════════════════════════════
    with T3:
        if not SS.amp_files:
            st.info("Complete the **Import & Configure** step first.")
        else:
            # ── Dataset selector ────────────────────────────────────────────────
            _file_names_cal = [f["filename"] for f in SS.amp_files]
            if SS.get("cal_active_file") not in _file_names_cal:
                SS["cal_active_file"] = _file_names_cal[0]
            if len(_file_names_cal) > 1:
                st.selectbox(
                    "Dataset",
                    _file_names_cal,
                    key="cal_active_file",
                    help="Each imported file has its own calibration table — pick "
                         "which one to edit below.",
                )
            _active_fi   = _file_names_cal.index(SS["cal_active_file"])
            _active_frec = SS.amp_files[_active_fi]
    
            # ── Analysis settings ─────────────────────────────────────────────
            st.subheader("Analysis Settings")
            if SS.smooth_method != "None":
                st.caption(
                    f"Averaging below uses the **{SS.smooth_method}** smoothing "
                    "configured in the Time Series tab."
                )
            _cal_multi_file = len(SS.amp_files) > 1
            # Store the live file dict (not a snapshot of its cpdf) so that any
            # updates made below — including the auto-recompute inside Compute
            # Calibration itself — are picked up without a stale-copy bug.
            _cal_combo_lookup = {
                _amp_label(frec["filename"], ch["name"], _cal_multi_file): (frec, ch)
                for frec in SS.amp_files
                for ch in frec["channels"]
            }
            a1, a2, a3 = st.columns(3)
            analyze_chs = a1.multiselect(
                "Channels to analyse",
                list(_cal_combo_lookup.keys()),
                default=list(_cal_combo_lookup.keys())[:1],
                help="Select one or more channels (and, with multiple files loaded, file·channel pairs). "
                     "Each uses its own dataset's calibration table above.",
            )
            fit_type = a2.selectbox(
                "Fit type",
                ["Linear", "Segmented Linear"],
                help=(
                    "**Linear** — single straight-line fit across all concentrations.\n\n"
                    "**Segmented Linear** — piecewise fit for sensors with two linear "
                    "dynamic ranges (e.g. different slopes at low vs high concentration). "
                    "Breakpoints are found automatically."
                ),
            )
            n_seg = (int(a3.number_input(
                        "Segments", 2, 4, 2,
                        help="Number of linear segments. 2 = one breakpoint, 3 = two breakpoints.",
                     ))
                     if fit_type == "Segmented Linear" else 1)
    
            show_avg = (
                st.checkbox(
                    "Add channel average trace",
                    value=False,
                    help=(
                        "Plots the element-wise mean of all selected channels as an "
                        "additional trace (black diamonds). Error bars show the "
                        "channel-to-channel standard deviation at each step, and "
                        "LOD/LOQ are based on the propagated blank noise."
                    ),
                )
                if len(analyze_chs) >= 2 else False
            )
    
            with st.expander("Quick-fill: common calibration protocols"):
                _preset_name = st.selectbox(
                    "Preset", list(_AMP_PRESETS.keys()), key="amp_cal_preset_choice",
                )
                _preset = _AMP_PRESETS[_preset_name]
                p1, p2, p3 = st.columns(3)
                _preset_start = p1.number_input(
                    "Start time (s)", min_value=0.0, value=float(_preset["start"]),
                    format="%.5g", key="amp_preset_start",
                    help="When the first spike's averaging window begins.",
                )
                _preset_interval_str = p2.text_input(
                    "Interval (s)", value=str(_preset["interval"]),
                    key="amp_preset_interval",
                    help="Duration held at each step before the next spike. Enter one "
                         "number to apply it to every step, or comma-separate a value "
                         "per step (must match the number of increments below) for "
                         "unevenly-spaced steps, e.g. 300, 300, 600, 900.",
                )
                _preset_avg_window = p3.number_input(
                    "Avg window (s)", min_value=0.001, value=60.0,
                    format="%.5g", key="amp_preset_avg_window",
                    help="Average only the last N seconds of each interval (avoids the "
                         "transient right after each spike).",
                )
                _preset_incr_str = st.text_input(
                    f"Concentration increments ({SS.conc_unit}), comma-separated — cumulative "
                    "(each spike adds to the running total). Add more to extend the series.",
                    value=", ".join(str(v) for v in _preset["increments"]),
                    key="amp_preset_increments",
                )
                p4, p5 = st.columns(2)
                _preset_stock_conc = p4.number_input(
                    f"Stock Conc ({SS.conc_unit})", min_value=0.0, value=0.0,
                    format="%.5g", key="amp_preset_stock_conc",
                    help="Optional — only needed if you also want Spike Vol back-filled "
                         "for you. Leave at 0 to skip: the concentration increments above "
                         "are used directly as the Concentration column. Set this above "
                         "the largest cumulative target to also back-solve Spike Vol from "
                         "this stock concentration and Initial Volume.",
                )
                _preset_initial_volume = p5.number_input(
                    f"Initial Volume ({SS.vol_unit})", min_value=0.0, value=float(SS.initial_volume),
                    format="%.5g", key="amp_preset_initial_volume",
                    help="Volume of buffer/blank in the vessel before any spikes — same "
                         "value as Initial volume below; changing it here updates that too. "
                         "Only used if Stock Conc above is set.",
                )
                _preset_include_blank = st.checkbox(
                    "Include Blank/baseline row (0 → start time)", value=True,
                    key="amp_preset_include_blank",
                )
                if st.button("Apply preset — replaces the table below", key="apply_amp_preset"):
                    try:
                        _increments = [float(v.strip()) for v in _preset_incr_str.split(",") if v.strip()]
                        if not _increments:
                            raise ValueError("no concentration increments given")
                        _interval_parts = [float(v.strip()) for v in _preset_interval_str.split(",") if v.strip()]
                        if not _interval_parts:
                            raise ValueError("no interval given")
                        if len(_interval_parts) == 1:
                            _intervals = _interval_parts * len(_increments)
                        elif len(_interval_parts) == len(_increments):
                            _intervals = _interval_parts
                        else:
                            raise ValueError(
                                f"interval count ({len(_interval_parts)}) must be 1 (applied to "
                                f"every step) or match the number of increments ({len(_increments)})"
                            )
                    except ValueError as e:
                        st.error(f"Couldn't parse increments/interval — {e}. Use comma-separated "
                                 "numbers, e.g. 25, 25, 50, 50 for increments and either one "
                                 "interval for all steps or one per step, e.g. 300, 300, 600, 600.")
                    else:
                        try:
                            _active_frec["cpdf"] = _preset_cpdf_amp(
                                _increments, _preset_start, _intervals, _preset_include_blank,
                                _preset_stock_conc, _preset_initial_volume, _preset_avg_window)
                        except ValueError as e:
                            st.error(f"Couldn't back-solve Spike Vol: {e} (largest target here is "
                                     f"{sum(_increments):.5g} {SS.conc_unit}). Set Stock Conc to 0 "
                                     "to skip the back-solve and use the concentrations as-is.")
                        else:
                            SS.initial_volume = _preset_initial_volume
                            SS.amp_files_cal_editor_version = SS.get("amp_files_cal_editor_version", 0) + 1
                            st.success(f"Preset applied — {len(_increments) + int(_preset_include_blank)} "
                                       "rows. Edit any cell below, or add more rows with the grid's ➕ button.")
                            st.rerun()

            _render_autodetect_expander(
                files_key="amp_files",
                active_frec=_active_frec,
                build_cpdf_fn=_cpdf_from_autodetect_windows,
                key_prefix="amp",
                has_baseline=True,
            )

            # ── Calibration-point editor ──────────────────────────────────────
            st.subheader(
                "Calibration Points"
                + (f" — {_active_frec['filename']}" if len(_file_names_cal) > 1 else "")
            )
            st.caption(
                "Add one row per concentration step. "
                "**t start / t end** define the averaging window — read these off the "
                "time-series chart. "
                "Check **Baseline?** on the blank or buffer row; its average current "
                "is subtracted from all other steps. "
                "**Spike Vol / Stock Conc** are optional — fill them in to use the "
                "effective concentration calculator below instead of typing "
                "Concentration by hand. "
                + ("Each imported file keeps its own table, so switch **Dataset** "
                   "above to edit another one." if len(_file_names_cal) > 1 else "")
            )
            if "amp_files_cal_editor_version" not in SS:
                SS.amp_files_cal_editor_version = 0
            with st.form(key=f"amp_cal_form_{_active_fi}"):
                _cpdf_edit = st.data_editor(
                    _active_frec["cpdf"],
                    key=f"cal_editor_{_active_fi}_{SS.amp_files_cal_editor_version}",
                    num_rows="dynamic",
                    use_container_width=True,
                    column_config={
                        "Label":         st.column_config.TextColumn(
                            "Label",
                            help="Short name shown on the plot, e.g. 'Blank', '0.5 mM'",
                        ),
                        "Concentration": st.column_config.NumberColumn(
                            f"Concentration ({SS.conc_unit})",
                            format="%.5g",
                            help="Analyte concentration for this step",
                        ),
                        "Spike Vol": st.column_config.NumberColumn(
                            f"Spike Vol ({SS.vol_unit})",
                            format="%.5g",
                            help="Optional: volume of stock solution spiked in at this step. "
                                 "Used by the effective concentration calculator below.",
                        ),
                        "Stock Conc": st.column_config.NumberColumn(
                            f"Stock Conc ({SS.conc_unit})",
                            format="%.5g",
                            help="Optional: concentration of the stock solution used for this step's spike.",
                        ),
                        "t_start": st.column_config.NumberColumn(
                            "t start (s)",
                            help="Start of the averaging window (seconds)",
                        ),
                        "t_end": st.column_config.NumberColumn(
                            "t end (s)",
                            help="End of the averaging window (seconds)",
                        ),
                        "avg_duration": st.column_config.NumberColumn(
                            "Avg window (s)",
                            format="%.4g",
                            help="If set, t start = t end − this value (overrides t start)",
                        ),
                        "Baseline": st.column_config.CheckboxColumn(
                            "Baseline?",
                            help="Tick for the blank / buffer step. Its current is subtracted from all other steps.",
                        ),
                    },
                )
                # Persist every edit immediately so any other code running later in
                # this same pass (dataset switches, Compute Calibration, session
                # export) always sees the latest typed values, not a stale copy.
                _active_frec["cpdf"] = _cpdf_edit
    
                with st.expander("Effective concentration calculator (serial dilution)"):
                    st.caption(
                        "Models a single vessel that starts at **Initial Volume** of blank "
                        "buffer. Each row's **Spike Vol** of **Stock Conc** analyte is added "
                        "in sequence (top to bottom); computing the calibration below "
                        "automatically applies this to the **Concentration** column. "
                        "**t start** is filled in from **Avg window (s)** at the same time, "
                        "for any row where that column is set. Blank Spike Vol / Stock Conc "
                        "cells are treated as 0. Use the button below to preview the result "
                        "here without running the full calibration yet."
                    )
                    v1, v2 = st.columns(2)
                    SS.initial_volume = v1.number_input(
                        "Initial volume", min_value=0.0, value=float(SS.initial_volume),
                        format="%.5g",
                        help="Volume of buffer/blank in the vessel before any spikes are added.",
                    )
                    SS.vol_unit = v2.text_input(
                        "Volume unit", SS.vol_unit, help="e.g. mL, µL, L",
                    )
                    if st.form_submit_button("Preview: update Concentration & t start"):
                        _active_frec["cpdf"] = _apply_effective_concentration(_cpdf_edit, SS.initial_volume)
                        SS.amp_files_cal_editor_version += 1
                        st.success("Concentration / t start updated above.")
                        st.rerun()
    
                st.divider()
    
                def _do_compute_calibration() -> bool:
                    """Runs the full per-channel calibration compute, each channel
                    pulling its OWN dataset's current calibration table. Returns
                    True iff at least one channel produced results."""
                    results = {}
                    for ch_name in analyze_chs:
                        frec, ch = _cal_combo_lookup[ch_name]
                        cpdf = (frec["cpdf"]
                                .dropna(subset=["t_end"])
                                .reset_index(drop=True))
                        if cpdf.empty:
                            st.error(f"**{ch_name}**: no valid calibration rows — fill in "
                                     f"{frec['filename']}'s table above.")
                            continue
    
                        base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
                        base_idx  = int(base_rows.index[0]) if len(base_rows) else 0
                        if len(base_rows) == 0:
                            st.warning(f"**{ch_name}**: no baseline row marked in "
                                       f"{frec['filename']} — using the first row as baseline.")
    
                        df = frec["df"]
                        t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
                        i_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                        i_arr = smooth_signal(i_arr, SS.smooth_method, SS.smooth_window, SS.smooth_polyorder)
    
                        avgs, sigs, n_pts, t_starts_used = [], [], [], []
                        for _, row in cpdf.iterrows():
                            _ets = _eff_t_start(row)
                            t_starts_used.append(_ets)
                            if _ets is None:
                                avgs.append(np.nan)
                                sigs.append(np.nan)
                                n_pts.append(0)
                                continue
                            mask = (t_arr >= _ets) & (t_arr <= row["t_end"])
                            pts  = i_arr[mask]
                            pts  = pts[~np.isnan(pts)]
                            n_pts.append(int(pts.size))
                            avgs.append(float(np.mean(pts)) if pts.size > 0  else np.nan)
                            # ddof=1 (sample SD); NaN when < 2 points — avoids false σ=0
                            sigs.append(float(np.std(pts, ddof=1)) if pts.size >= 2 else np.nan)
    
                        # Warn about windows with insufficient data
                        _win_issues = []
                        for _wlbl, _wavg, _wsig in zip(cpdf["Label"], avgs, sigs):
                            if np.isnan(_wavg):
                                _win_issues.append(f"**{_wlbl}**: no data points in window")
                            elif np.isnan(_wsig):
                                _win_issues.append(f"**{_wlbl}**: only 1 point — σ undefined")
                        if _win_issues:
                            st.warning(f"{ch_name} — " + "; ".join(_win_issues)
                                       + ". Adjust t start / t end.")
    
                        base_val = avgs[base_idx]
                        sigma_bl = sigs[base_idx]  # NaN if baseline window has < 2 points
                        if np.isnan(base_val):
                            _bl_lbl = cpdf.at[base_idx, 'Label']
                            st.error(
                                f"**{ch_name}**: baseline window (row '{_bl_lbl}') has no data"
                                " points — ΔI cannot be computed. "
                                "Adjust the baseline t start / t end to overlap the signal data."
                            )
                            continue
                        delta_i  = [
                            (v - base_val) if not np.isnan(v) else np.nan
                            for v in avgs
                        ]
    
                        results[ch_name] = dict(
                            concs          = cpdf["Concentration"].values.astype(float),
                            labels         = cpdf["Label"].values,
                            avgs           = avgs,
                            sigs           = sigs,
                            delta_i        = delta_i,
                            sigma_bl       = float(sigma_bl),   # NaN propagates → LOD/LOQ show "—"
                            is_average     = False,
                            n_pts          = n_pts,
                            t_starts_used  = t_starts_used,
                            t_ends         = cpdf["t_end"].tolist(),
                            baselines      = cpdf["Baseline"].tolist(),
                        )
    
                    # ── Channel average ───────────────────────────────────────────
                    _avg_chs = [c for c in analyze_chs if c in results]
                    if show_avg and len(_avg_chs) >= 2:
                        all_di    = np.array([results[c]["delta_i"] for c in _avg_chs],
                                             dtype=float)
                        all_avgs  = np.array([results[c]["avgs"]    for c in _avg_chs],
                                             dtype=float)
                        all_sigma = [results[c]["sigma_bl"] for c in _avg_chs]
                        n_ch      = len(_avg_chs)
    
                        avg_delta_i   = np.nanmean(all_di, axis=0)
                        std_across_ch = np.nanstd(all_di, axis=0, ddof=1)  # inter-channel spread (sample std)
                        avg_avgs      = np.nanmean(all_avgs, axis=0)
                        # propagated blank noise: sqrt(Σσi²) / n_ch (all channels, not just those with finite σ)
                        _valid_s = [s for s in all_sigma if np.isfinite(s)]
                        sigma_bl_avg = (np.sqrt(sum(s**2 for s in _valid_s)) / n_ch
                                        if _valid_s else np.nan)
    
                        results["Channel Average"] = dict(
                            concs      = results[_avg_chs[0]]["concs"],
                            labels     = results[_avg_chs[0]]["labels"],
                            avgs       = avg_avgs.tolist(),
                            sigs       = std_across_ch.tolist(),
                            delta_i    = avg_delta_i.tolist(),
                            sigma_bl   = float(sigma_bl_avg),
                            is_average = True,
                            baselines  = results[_avg_chs[0]]["baselines"],
                        )
    
                    if not results:
                        SS.cal_results = None
                        return False
                    SS.cal_results = dict(results=results, fit_type=fit_type, n_seg=n_seg)
                    return True
    
                # A single, robust action: type values into the table above, click
                # this once, and everything downstream — effective-concentration
                # derivation, the active dataset's table display, and the
                # calibration curve/statistics below — updates together. Wrapping
                # the editor and this button in one st.form means the button's
                # rerun reads each widget's live value at submit time, instead of
                # depending on a separate blur event racing the click.
                compute_clicked = st.form_submit_button("Compute Calibration", type="primary")
    
            if compute_clicked:
                if not analyze_chs:
                    st.error("Select at least one channel to analyse above.")
                else:
                    _calc_df = _apply_effective_concentration(_cpdf_edit, SS.initial_volume)
                    _active_frec["cpdf"] = _calc_df
                    if not _calc_df.equals(_cpdf_edit):
                        # Table content changed (e.g. Spike Vol/Stock Conc derived
                        # new Concentration values) — bump + rerun so the editor
                        # widget itself refreshes to show them, then finish the
                        # compute automatically on the very next pass.
                        SS.amp_files_cal_editor_version += 1
                        SS["_cal_pending_compute"] = True
                        st.rerun()
                    else:
                        SS["_cal_computed_msg"] = (
                            "Calibration computed — results below."
                            if _do_compute_calibration() else None
                        )
    
            if SS.pop("_cal_pending_compute", False):
                SS["_cal_computed_msg"] = (
                    "Calibration computed — results below."
                    if _do_compute_calibration() else None
                )
            if SS.get("_cal_computed_msg"):
                st.success(SS.pop("_cal_computed_msg"))
    
            # ── Plot & statistics ─────────────────────────────────────────────
            if SS.cal_results:
                res_map   = SS.cal_results["results"]
                ft        = SS.cal_results["fit_type"]
                ns        = SS.cal_results["n_seg"]
    
                # ── Averaging window details ───────────────────────────────────
                _res_map_d: dict[str, dict] = res_map  # type: ignore[assignment]
                with st.expander("Averaging window details", expanded=True):
                    for _wch, _wres in _res_map_d.items():
                        if _wres.get("is_average"):
                            continue
                        st.markdown(f"**{_wch}**")
                        _wd = []
                        for _wi, _wlbl in enumerate(_wres["labels"]):
                            _ts = (_wres["t_starts_used"][_wi]
                                   if _wi < len(_wres.get("t_starts_used", [])) else None)
                            _te = (_wres["t_ends"][_wi]
                                   if _wi < len(_wres.get("t_ends", [])) else np.nan)
                            _is_bl = (_wres["baselines"][_wi]
                                      if _wi < len(_wres.get("baselines", [])) else False)
                            _wd.append({
                                "Label":                      str(_wlbl),
                                "t start (s)":                f"{_ts:.4g}" if _ts is not None else "—",
                                "t end (s)":                  f"{float(_te):.4g}" if np.isfinite(float(_te)) else "—",
                                "N points":                   _wres["n_pts"][_wi] if _wi < len(_wres.get("n_pts", [])) else "—",
                                f"Mean ({SS.cur_unit})":      fmt(_wres["avgs"][_wi]),
                                f"SD ({SS.cur_unit})":        fmt(_wres["sigs"][_wi]),
                                f"ΔI ({SS.cur_unit})":        fmt(_wres["delta_i"][_wi]),
                                "Baseline":                   "✓" if _is_bl else "",
                            })
                        st.dataframe(pd.DataFrame(_wd), hide_index=True, use_container_width=True)
    
                fig_cal   = go.Figure()
                stat_rows = []
    
                for j, (ch_name, res) in enumerate(res_map.items()):
                    is_avg = res.get("is_average", False)
                    col    = AVG_COLOR if is_avg else PAL[j % len(PAL)]
                    # Exclude the blank/baseline point from the plotted curve and
                    # the fit — it's ΔI = 0 by construction and isn't a real
                    # calibration step. Still shown in "Averaging window details".
                    _keep  = _baseline_keep_mask(res.get("baselines", [False] * len(res["concs"])))
                    x      = np.asarray(res["concs"], dtype=float)[_keep]
                    y      = np.array(res["delta_i"], float)[_keep]
                    labels_plot = np.asarray(res["labels"], dtype=object)[_keep]
                    sigs_plot   = np.asarray(res["sigs"], dtype=float)[_keep]
                    marker_sym = "diamond" if is_avg else "circle"
    
                    fig_cal.add_trace(go.Scatter(
                        x=x, y=y,
                        name=ch_name,
                        mode="markers+text",
                        text=labels_plot,
                        textposition="top center",
                        textfont=dict(size=10),
                        marker=dict(color=col, size=10, symbol=marker_sym,
                                    line=dict(width=1.5, color="white")),
                        error_y=dict(
                            type="data",
                            array=[float(s) if (s and not np.isnan(s)) else 0.0
                                   for s in sigs_plot],
                            visible=is_avg, color=col,
                            thickness=1.5, width=4,
                        ),
                    ))
    
                    _pf = piecewise_fit(x, y, int(ns) if ft == "Segmented Linear" else 1)
                    segs, breakpoints = _pf["segments"], _pf["breakpoints"]
                    for k, seg in enumerate(segs):
                        xp = np.linspace(seg["xr"][0], seg["xr"][1], 300)
                        yp = seg["slope"] * xp + seg["intercept"]
                        lbl = ch_name + (f" seg {k + 1}" if len(segs) > 1 else "")
                        fig_cal.add_trace(go.Scatter(
                            x=xp, y=yp,
                            name=f"{lbl} fit",
                            mode="lines",
                            showlegend=False,
                            line=dict(color=col,
                                      dash="dot" if is_avg else "dash",
                                      width=2),
                        ))
    
                        sigma   = res["sigma_bl"]
                        sens    = seg["slope"]
                        lod_val = (3.0  * abs(sigma) / abs(sens)) if sens else np.nan
                        loq_val = (10.0 * abs(sigma) / abs(sens)) if sens else np.nan
    
                        stat_rows.append({
                            "Channel": ch_name,
                            "Segment": (
                                f"{seg['xr'][0]:.3g}–{seg['xr'][1]:.3g} {SS.conc_unit}"
                                if len(segs) > 1 else "Full range"
                            ),
                            f"Sensitivity ({SS.cur_unit}/{SS.conc_unit})": fmt(sens),
                            "Intercept": fmt(seg["intercept"]),
                            "R²": f"{seg['r2']:.4f}",
                            f"LOD ({SS.conc_unit})": fmt(lod_val),
                            f"LOQ ({SS.conc_unit})": fmt(loq_val),
                            f"σ blank ({SS.cur_unit})": fmt(sigma),
                        })
    
                    for bp in breakpoints:
                        fig_cal.add_vline(
                            x=bp, line_dash="dot", line_color=col, line_width=1.5,
                            annotation_text=f"{bp:.3g} {SS.conc_unit}",
                            annotation_position="top",
                            annotation_font_color=col,
                        )
    
                _pt_cal = _plot_theme()
                fig_cal.update_layout(
                    xaxis_title=f"Concentration ({SS.conc_unit})",
                    yaxis_title=f"ΔI ({SS.cur_unit})",
                    hovermode="closest",
                    height=520,
                    template=_pt_cal["template"],
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    hoverdistance=40,
                    xaxis=dict(
                        showspikes=True, spikemode="across", spikesnap="cursor",
                        spikecolor=_pt_cal["spike"], spikethickness=1, spikedash="dot",
                        showgrid=True, gridcolor=_pt_cal["grid"],
                        linecolor=_pt_cal["axisline"],
                    ),
                    yaxis=dict(
                        showspikes=True, spikemode="across",
                        spikecolor=_pt_cal["spike"], spikethickness=1, spikedash="dot",
                        showgrid=True, gridcolor=_pt_cal["grid"],
                        linecolor=_pt_cal["axisline"],
                        zeroline=True, zerolinecolor=_pt_cal["axisline"],
                    ),
                )
                st.plotly_chart(fig_cal, use_container_width=True, key="amp_cal_chart",
                                config={"scrollZoom": True, "displayModeBar": True,
                                        "modeBarButtonsToRemove": ["select2d", "lasso2d"]})
                SS.cal_fig = fig_cal
    
                if stat_rows:
                    st.subheader("Statistics")
                    with st.expander("What do these metrics mean?", expanded=False):
                        st.markdown(f"""
    | Metric | Meaning |
    |---|---|
    | **Sensitivity** | Slope of the fit line — the current change per unit concentration ({SS.cur_unit}/{SS.conc_unit}) |
    | **Intercept** | Fitted current at zero analyte concentration |
    | **R²** | Coefficient of determination — closer to 1.0 indicates a better fit |
    | **LOD** | Limit of Detection = 3 × σ_blank / sensitivity. Smallest concentration distinguishable from noise. |
    | **LOQ** | Limit of Quantification = 10 × σ_blank / sensitivity. Smallest concentration reliably quantifiable. |
    | **σ blank** | Standard deviation of the current during the baseline averaging window — a measure of baseline noise. |
    """)
                    st.dataframe(
                        pd.DataFrame(stat_rows),
                        use_container_width=True,
                        hide_index=True,
                    )
    
                    _render_ai_insights_section(res_map, ft, key_prefix="amp")
    
                dl1, dl2 = st.columns(2)
                dl1.download_button(
                    "Download as interactive HTML",
                    data=fig_cal.to_html(include_plotlyjs="cdn"),
                    file_name="calibration_curve.html",
                    mime="text/html",
                )
                cal_png_bytes = render_cal_png(
                    res_map, ft, int(ns), SS.conc_unit, SS.cur_unit  # type: ignore[arg-type]
                )
                dl2.download_button(
                    "Download as PNG",
                    data=cal_png_bytes,
                    file_name="calibration_curve.png",
                    mime="image/png",
                )
    
    
    # ═════════════════════════════════════════════════════════════════════════════
    # TAB 4 · Export
    # ═════════════════════════════════════════════════════════════════════════════
    with T4:
        st.subheader("Export")
        st.caption("All exports are also available inline on the Time Series and Calibration Curve tabs.")
    
        if SS.cal_results:
            st.markdown("#### Calibration summary table")
            st.caption(
                "One row per concentration step per channel. "
                "Avg Current is the mean over the defined window; "
                "ΔI is baseline-corrected."
            )
            rows_out = []
            cal_res: dict = SS.cal_results["results"]  # type: ignore[assignment]
            for ch_name, res in cal_res.items():
                for lbl, conc, avg, sig, di in zip(
                    res["labels"], res["concs"],
                    res["avgs"], res["sigs"], res["delta_i"]
                ):
                    rows_out.append({
                        "Channel":                          ch_name,
                        "Label":                            lbl,
                        f"Concentration ({SS.conc_unit})":  conc,
                        f"Avg Current ({SS.cur_unit})":     avg,
                        f"SD ({SS.cur_unit})":              sig,
                        f"ΔI ({SS.cur_unit})":              di,
                    })
            export_df = pd.DataFrame(rows_out)
            st.dataframe(export_df, use_container_width=True, hide_index=True)
    
            st.markdown("#### Calibration curve downloads")
            dl1, dl2, dl3 = st.columns(3)
            dl1.download_button(
                "Calibration CSV",
                data=export_df.to_csv(index=False).encode(),
                file_name="calibration_data.csv",
                mime="text/csv",
            )
            if SS.cal_fig is not None:
                dl2.download_button(
                    "Plot — interactive HTML",
                    data=SS.cal_fig.to_html(include_plotlyjs="cdn"),
                    file_name="calibration_curve.html",
                    mime="text/html",
                )
                _cr = SS.cal_results
                cal_png_bytes = render_cal_png(
                    dict(_cr["results"]), str(_cr["fit_type"]), int(_cr["n_seg"]),  # type: ignore[index]
                    SS.conc_unit, SS.cur_unit,
                )
                dl3.download_button(
                    "Plot — PNG (150 dpi)",
                    data=cal_png_bytes,
                    file_name="calibration_curve.png",
                    mime="image/png",
                )
        else:
            st.info("Run calibration analysis in the **Calibration Curve** tab first.")
    
        if SS.amp_files:
            st.divider()
            st.markdown("#### Time-series downloads")
            dl4, dl5, dl6 = st.columns(3)
            with dl4:
                for _fi4, _frec in enumerate(SS.amp_files):
                    st.download_button(
                        f"Raw data CSV — {_frec['filename']}",
                        data=_frec["df"].to_csv(index=False).encode(),
                        file_name=f"raw_{_frec['filename']}.csv" if not _frec["filename"].endswith(".csv") else f"raw_{_frec['filename']}",
                        mime="text/csv",
                        key=f"raw_dl_{_fi4}_{_frec['filename']}",
                    )
            all_ch_names_export = [
                _amp_label(f["filename"], c["name"], len(SS.amp_files) > 1)
                for f in SS.amp_files for c in f["channels"]
            ]
            _amp_ts_fig = SS.get("amp_files_ts_fig")
            if _amp_ts_fig is not None:
                dl5.download_button(
                    "Plot — interactive HTML",
                    data=_amp_ts_fig.to_html(include_plotlyjs="cdn"),
                    file_name="time_series.html",
                    mime="text/html",
                )
                ts_vis = SS.get("amp_files_ts_vis_ms") or all_ch_names_export
                ts_png_bytes = render_ts_png(
                    SS.amp_files, SS.cur_unit, ts_vis,
                    smooth_method=SS.smooth_method,
                    smooth_window=SS.smooth_window,
                    smooth_polyorder=SS.smooth_polyorder,
                )
                dl6.download_button(
                    "Plot — PNG (150 dpi)",
                    data=ts_png_bytes,
                    file_name="time_series.png",
                    mime="image/png",
                )
    
        # ── Publication-quality export ────────────────────────────────────────────
        if SS.amp_files or SS.cal_results:
            st.divider()
            st.markdown("#### Publication-quality export")
            with st.expander("Export settings", expanded=True):
                _pc1, _pc2, _pc3, _pc4 = st.columns(4)
                _pstyle = _pc1.selectbox(
                    "Style", ["Origin", "Minimal"], key="pub_style",
                    help="**Origin**: four-sided box axes, inward ticks, square legend — matches OriginPro defaults.\n\n**Minimal**: open axes (no top/right spines), compact fonts.",
                )
                _pfmt = _pc2.selectbox(
                    "Format", ["SVG", "PNG", "PDF", "TIFF"], key="pub_fmt",
                    help="SVG/PDF are vector — infinitely scalable and editable in Illustrator / Inkscape.",
                )
                _pdpi = _pc3.segmented_control(
                    "DPI", options=[150, 300, 600], default=300, key="pub_dpi",
                    disabled=_pfmt in ["SVG", "PDF"],
                    help="Ignored for SVG/PDF.",
                )
                _psize_label = _pc4.selectbox(
                    "Width", ["Single (3.5\")", "1.5-col (5\")", "Double (7\")", "Full (6.5\")"],
                    key="pub_size",
                )
            _psize_map = {
                "Single (3.5\")": (3.5, 2.625),
                "1.5-col (5\")":  (5.0, 3.75),
                "Double (7\")":   (7.0, 5.0),
                "Full (6.5\")":   (6.5, 4.5),
            }
            _pfs      = _psize_map[_psize_label]
            # segmented_control can be clicked off to None (no `required` kwarg
            # in current Streamlit to prevent that) — fall back to the same
            # 300 default used elsewhere rather than crashing on int(None).
            _pdpi_val = int(_pdpi) if (_pdpi is not None and _pfmt not in ["SVG", "PDF"]) else 300
            _pfmt_l   = _pfmt.lower()
            _pstyle_l = _pstyle.lower()
    
            _pa, _pb = st.columns(2)
            if SS.amp_files:
                _ts_vis  = SS.get("amp_files_ts_vis_ms") or [
                    _amp_label(f["filename"], c["name"], len(SS.amp_files) > 1)
                    for f in SS.amp_files for c in f["channels"]
                ]
                _prev_ts = render_ts_png(SS.amp_files, SS.cur_unit, _ts_vis,
                                         dpi=96, fmt="png", figsize=_pfs, style=_pstyle_l,
                                         smooth_method=SS.smooth_method,
                                         smooth_window=SS.smooth_window,
                                         smooth_polyorder=SS.smooth_polyorder)
                _pub_ts  = render_ts_png(SS.amp_files, SS.cur_unit, _ts_vis,
                                         dpi=_pdpi_val, fmt=_pfmt_l, figsize=_pfs, style=_pstyle_l,
                                         smooth_method=SS.smooth_method,
                                         smooth_window=SS.smooth_window,
                                         smooth_polyorder=SS.smooth_polyorder)
                with _pa:
                    st.caption("Time series preview")
                    st.image(_prev_ts, use_container_width=True)
                    st.download_button(f"Download ({_pfmt})", data=_pub_ts,
                                       file_name=f"time_series_pub.{_pfmt_l}",
                                       mime=_MIME[_pfmt_l], use_container_width=True, key="pub_ts_dl")
            if SS.cal_results:
                _cr       = SS.cal_results
                _prev_cal = render_cal_png(dict(_cr["results"]), str(_cr["fit_type"]), int(_cr["n_seg"]),  # type: ignore[index]
                                           SS.conc_unit, SS.cur_unit,
                                           dpi=96, fmt="png", figsize=_pfs, style=_pstyle_l)
                _pub_cal  = render_cal_png(dict(_cr["results"]), str(_cr["fit_type"]), int(_cr["n_seg"]),  # type: ignore[index]
                                           SS.conc_unit, SS.cur_unit,
                                           dpi=_pdpi_val, fmt=_pfmt_l, figsize=_pfs, style=_pstyle_l)
                with _pb:
                    st.caption("Calibration curve preview")
                    st.image(_prev_cal, use_container_width=True)
                    st.download_button(f"Download ({_pfmt})", data=_pub_cal,
                                       file_name=f"calibration_pub.{_pfmt_l}",
                                       mime=_MIME[_pfmt_l], use_container_width=True, key="pub_cal_dl")
    
    
