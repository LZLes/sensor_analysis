"""Solid-State (potentiometric ISE/ISFET) mode: Nernstian E vs log10(Concentration)
calibration — no Baseline/blank-subtraction, no dilution calculator (unlike Amperometry)."""

import io
import os

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ai_insights import _render_ai_insights_section
from core.constants import PAL, _SAMPLE_DATA_DIR, _plot_theme, fmt
from core.numeric import lin_reg, to_num, _eff_t_start
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style
from core.shared_tabs import (
    _amp_label, _render_autodetect_expander, _render_import_tab,
    _render_timeseries_tab, render_ts_png,
)

SS = st.session_state


def _cpdf_from_autodetect_windows(windows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """Turns generic (label, t_start, t_end) triples from Auto-detect into a
    Solid-State-schema calibration table. Concentration is left blank —
    times are recoverable from the trace, concentrations aren't."""
    return pd.DataFrame({
        "Label":         [w[0] for w in windows],
        "Concentration": [0.0] * len(windows),
        "t_start":       [w[1] for w in windows],
        "t_end":         [w[2] for w in windows],
        "avg_duration":  [np.nan] * len(windows),
        "Reading_mV":    [np.nan] * len(windows),
    })


_GAS_CONSTANT_R = 8.314462618   # J/(mol*K)
_FARADAY_F      = 96485.33212   # C/mol


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
    which generally does not coincide with any input data point. This is
    NOT the same as piecewise_fit's continuous ("broken-stick") fit above:
    piecewise_fit forces its segments to meet exactly at a breakpoint that
    must land on an existing standard's x-value, which is a display-
    friendly continuous curve but not what "LOD" means in the ISE
    literature, where the two regimes are fit independently and the LOD is
    wherever those two (generally non-touching) lines would cross.

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


def render_solid_cal_png(res_map: dict, conc_unit: str, signal_unit: str,
                         dpi: int = 150, fmt: str = "png",
                         figsize: tuple | None = None, style: str = "default") -> bytes:
    """Matplotlib export for the Solid-State (Nernstian) calibration curve —
    mirrors render_cal_png()'s structure: E (mV) vs log10(Concentration),
    the two independently-fit segments, and an LOD marker instead of
    piecewise_fit's breakpoint lines."""
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11
    _lgfs = 7 if style == "minimal" else 9
    _afs = 6.5 if style == "minimal" else 7.5
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (8, 6))
        _annot_blocks = []
        for j, (ch_name, res) in enumerate(res_map.items()):
            col = PAL[j % len(PAL)]
            x = np.asarray(res["log_conc"], dtype=float)
            y = np.asarray(res["potential_mv"], dtype=float)
            ax.scatter(x, y, color=col, label=ch_name, marker="o", s=45,
                      edgecolors="white", linewidths=1.0, zorder=3)

            _low  = res.get("low_segment")
            _nern = res.get("nernstian_segment")
            _lod_log10 = res.get("lod_log10")
            _has_lod = _lod_log10 is not None and np.isfinite(_lod_log10)

            _ch_lines = [ch_name + ":"]
            for _seg, _seg_name, _ls in [(_low, "low", ":"), (_nern, "Nernstian", "--")]:
                if _seg is None:
                    continue
                if _seg_name == "low" and _has_lod:
                    _x0, _x1 = float(np.min(x)), _lod_log10
                elif _seg_name == "Nernstian" and _has_lod:
                    _x0, _x1 = _lod_log10, float(np.max(x))
                else:
                    _x0, _x1 = float(np.min(x)), float(np.max(x))
                if _x1 <= _x0:
                    _x0, _x1 = float(np.min(x)), float(np.max(x))
                xp = np.linspace(_x0, _x1, 200)
                yp = _seg["slope"] * xp + _seg["intercept"]
                ax.plot(xp, yp, linestyle=_ls, color=col, linewidth=2)
                s, b, r2 = _seg["slope"], _seg["intercept"], _seg["r2"]
                sign = "+" if b >= 0 else "−"
                _ch_lines.append(f"  {_seg_name}: y = {s:.3g}x {sign} {abs(b):.3g}   R² = {r2:.4f}")

            if _nern is not None:
                _pct = res.get("pct_of_ideal_nernstian")
                _pct_txt = f"{_pct:.1f}% of ideal" if _pct is not None else "—"
                _ch_lines.append(f"  Sens = {_nern['slope']:.3g} {signal_unit}/decade ({_pct_txt})")

            if _has_lod:
                ax.axvline(_lod_log10, linestyle="-.", color=col, linewidth=1.2)
                _lod_conc = res.get("lod_conc")
                _lod_conc_txt = f"{_lod_conc:.3g} {conc_unit}" if _lod_conc is not None and np.isfinite(_lod_conc) else "—"
                ax.annotate(f"LOD {_lod_conc_txt}", xy=(_lod_log10, 1),
                           xycoords=("data", "axes fraction"),
                           xytext=(2, -2), textcoords="offset points",
                           fontsize=_afs, color=col, rotation=90, va="top", ha="left")
                _ch_lines.append(f"  LOD = {_lod_conc_txt}")

            _annot_blocks.append("\n".join(_ch_lines))

        ax.set_xlabel(f"log₁₀(Concentration [{conc_unit}])", fontsize=_lfs)
        ax.set_ylabel(f"Potential ({signal_unit})", fontsize=_lfs)
        ax.legend(fontsize=_lgfs, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        if _annot_blocks:
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


def _default_solid_cpdf() -> pd.DataFrame:
    """Starter calibration table for solid-state (potentiometric) sensors.
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


def _seed_solid_cpdf_for_new_file() -> pd.DataFrame:
    """Starter calibration table for a newly-imported solid-state file."""
    return _default_solid_cpdf()


_SOLID_PRESETS = {
    "Serial standards: 10, 25, 50, 100": {
        "values": [10, 25, 50, 100],
        "start": 600.0,
        "interval": 600.0,
    },
}


def _preset_cpdf_solid(values: list[float], start: float, interval: float,
                        avg_window: float) -> pd.DataFrame:
    """Builds a Solid-State calibration table from a series of absolute
    standard concentrations, each held for `interval` seconds, the first
    starting at `start`. avg_window fills Avg window (s) on every row."""
    n = len(values)
    t_starts = [start + i * interval for i in range(n)]
    t_ends   = [t + interval for t in t_starts]
    return pd.DataFrame({
        "Label":         [f"Std {i}" for i in range(1, n + 1)],
        "Concentration": list(values),
        "t_start":       t_starts,
        "t_end":         t_ends,
        "avg_duration":  [avg_window] * n,
        "Reading_mV":    [np.nan] * n,
    })


_SOLID_SAMPLE_STEPS = [
    ("Std 1", 1e-6,   0.0,  50.0),
    ("Std 2", 1e-5,  70.0, 110.0),
    ("Std 3", 1e-4, 130.0, 170.0),
    ("Std 4", 1e-3, 190.0, 230.0),
    ("Std 5", 1e-2, 250.0, 290.0),
    ("Std 6", 1e-1, 310.0, 350.0),
]
_SOLID_SAMPLE_FILES = ["solid_state_run.csv"]


def _solid_sample_cpdf() -> pd.DataFrame:
    return pd.DataFrame({
        "Label":         [s[0] for s in _SOLID_SAMPLE_STEPS],
        "Concentration": [s[1] for s in _SOLID_SAMPLE_STEPS],
        "t_start":       [s[2] for s in _SOLID_SAMPLE_STEPS],
        "t_end":         [s[3] for s in _SOLID_SAMPLE_STEPS],
        "avg_duration":  [np.nan] * len(_SOLID_SAMPLE_STEPS),
        "Reading_mV":    [np.nan] * len(_SOLID_SAMPLE_STEPS),
    })


def _load_solid_sample_data() -> list[dict] | None:
    """Reads the bundled solid_state_run.csv and returns a fully-configured
    solid_files entry (channel mapped, calibration table pre-filled), or
    None if the file isn't present (e.g. a stripped-down deployment)."""
    _files = []
    for _fn in _SOLID_SAMPLE_FILES:
        _path = os.path.join(_SAMPLE_DATA_DIR, _fn)
        if not os.path.isfile(_path):
            return None
        _df = pd.read_csv(_path)
        _channels = [{"name": "Electrode 1", "tc": "Time (s)", "ic": "Potential (mV)"}]
        _files.append({
            "filename": _fn, "df": _df, "channels": _channels,
            "cpdf": _solid_sample_cpdf(),
        })
    return _files


def render() -> None:
        ST1, ST2, ST3, ST4 = st.tabs([
            "① Import & Configure", "② Time Series", "③ Calibration Curve", "④ Export",
        ])
    
        with ST1:
            _render_import_tab(
                files_key="solid_files",
                signal_col_label="Potential",
                unit_key="solid_unit",
                active_file_key="solid_active_file",
                seed_cpdf_fn=_seed_solid_cpdf_for_new_file,
                sample_loader_fn=_load_solid_sample_data,
                sample_caption=(
                    "A synthetic potentiometric (ISE-style) run with a two-regime "
                    "response — a flattened low-concentration plateau and a "
                    "near-Nernstian high-concentration slope — and a ready-made "
                    "calibration table."
                ),
                sample_button_help=(
                    "Loads a bundled example potentiometric run with a pre-filled "
                    "calibration table so you can try this mode immediately."
                ),
                sample_loaded_msg=(
                    "Sample data loaded — head to the **Time Series** or "
                    "**Calibration Curve** tab to explore."
                ),
                sample_conc_unit="M",
                sample_signal_unit="mV",
                set_legacy_alias=False,
            )
            if SS.solid_files:
                st.info(
                    "**Solid-state calibration tables have no Baseline or dilution "
                    "calculator** — Nernstian fits use the raw potential directly, "
                    "and rows with Concentration ≤ 0 are excluded automatically "
                    "before the log₁₀ transform."
                )
    
        with ST2:
            _render_timeseries_tab(files_key="solid_files", unit_key="solid_unit",
                                   signal_axis_label="Potential")
    
        # ═════════════════════════════════════════════════════════════════════════
        # TAB 3 · Calibration Curve  (Nernstian: E vs log10(Concentration))
        # ═════════════════════════════════════════════════════════════════════════
        with ST3:
            if not SS.solid_files:
                st.info("Complete the **Import & Configure** step first.")
            else:
                _file_names_solid = [f["filename"] for f in SS.solid_files]
                if SS.get("solid_active_file") not in _file_names_solid:
                    SS["solid_active_file"] = _file_names_solid[0]
                if len(_file_names_solid) > 1:
                    st.selectbox(
                        "Dataset", _file_names_solid, key="solid_active_file",
                        help="Each imported file has its own calibration table — "
                             "pick which one to edit below.",
                    )
                _active_fi_s   = _file_names_solid.index(SS["solid_active_file"])
                _active_frec_s = SS.solid_files[_active_fi_s]
    
                # Analysis Settings lives outside the form (below) so it stays
                # live/reactive — only the editor itself needs form-batching to
                # avoid the blur-race, not this multiselect.
                st.subheader("Analysis Settings")
                _solid_multi_file = len(SS.solid_files) > 1
                _solid_combo_lookup = {
                    _amp_label(frec["filename"], ch["name"], _solid_multi_file): (frec, ch)
                    for frec in SS.solid_files
                    for ch in frec["channels"]
                }
                analyze_chs_solid = st.multiselect(
                    "Channels to analyse",
                    list(_solid_combo_lookup.keys()),
                    default=list(_solid_combo_lookup.keys())[:1],
                    help="Select one or more channels (and, with multiple files "
                         "loaded, file·channel pairs). Each uses its own "
                         "dataset's calibration table above.",
                )
    
                with st.expander("Quick-fill: common calibration protocols"):
                    _preset_name_s = st.selectbox(
                        "Preset", list(_SOLID_PRESETS.keys()), key="solid_cal_preset_choice",
                    )
                    _preset_s = _SOLID_PRESETS[_preset_name_s]
                    ps1, ps2, ps3 = st.columns(3)
                    _preset_start_s = ps1.number_input(
                        "Start time (s)", min_value=0.0, value=float(_preset_s["start"]),
                        format="%.5g", key="solid_preset_start",
                        help="When the first standard's averaging window begins.",
                    )
                    _preset_interval_s = ps2.number_input(
                        "Interval (s)", min_value=0.001, value=float(_preset_s["interval"]),
                        format="%.5g", key="solid_preset_interval",
                        help="Duration held at each standard before moving to the next.",
                    )
                    _preset_avg_window_s = ps3.number_input(
                        "Avg window (s)", min_value=0.001, value=60.0,
                        format="%.5g", key="solid_preset_avg_window",
                        help="Average only the last N seconds of each interval (avoids the "
                             "transient right after moving to a new standard).",
                    )
                    _preset_values_str_s = st.text_input(
                        f"Concentration values ({SS.conc_unit}), comma-separated — absolute, "
                        "one per standard. Add more to extend the series.",
                        value=", ".join(str(v) for v in _preset_s["values"]),
                        key="solid_preset_values",
                    )
                    if st.button("Apply preset — replaces the table below", key="apply_solid_preset"):
                        try:
                            _values_s = [float(v.strip()) for v in _preset_values_str_s.split(",") if v.strip()]
                            if not _values_s:
                                raise ValueError("empty")
                        except ValueError:
                            st.error("Couldn't parse the concentration values — use comma-separated "
                                     "numbers, e.g. 10, 25, 50, 100.")
                        else:
                            _active_frec_s["cpdf"] = _preset_cpdf_solid(
                                _values_s, _preset_start_s, _preset_interval_s, _preset_avg_window_s)
                            SS.cal_editor_version = SS.get("cal_editor_version", 0) + 1
                            st.success(f"Preset applied — {len(_values_s)} rows. Edit any cell below, "
                                       "or add more rows with the grid's ➕ button.")
                            st.rerun()

                _render_autodetect_expander(
                    files_key="solid_files",
                    active_frec=_active_frec_s,
                    build_cpdf_fn=_cpdf_from_autodetect_windows,
                    key_prefix="solid",
                    has_baseline=False,
                )

                st.subheader(
                    "Calibration Points"
                    + (f" — {_active_frec_s['filename']}" if len(_file_names_solid) > 1 else "")
                )
                st.caption(
                    "One row per standard. Fill in **Reading** directly, or leave it "
                    "blank and set **t start / t end** (read off the time-series chart) "
                    "to average the imported trace over that window instead. "
                    "**Concentration** must be > 0 — rows that aren't are excluded "
                    "automatically before fitting. "
                    + ("Each imported file keeps its own table, so switch **Dataset** "
                       "above to edit another one." if len(_file_names_solid) > 1 else "")
                )
                if "cal_editor_version" not in SS:
                    SS.cal_editor_version = 0
    
                # Only the editor + Compute button share the form now — so the
                # button's rerun reads the grid's live value at submit time,
                # rather than depending on a separate blur event racing the
                # click — while channel selection above stays reactive.
                with st.form(key=f"solid_cal_form_{_active_fi_s}"):
                    _scpdf_edit = st.data_editor(
                        _active_frec_s["cpdf"],
                        key=f"solid_cal_editor_{_active_fi_s}_{SS.cal_editor_version}",
                        num_rows="dynamic",
                        use_container_width=True,
                        column_config={
                            "Label": st.column_config.TextColumn(
                                "Label",
                                help="Short name shown on the plot, e.g. 'Std 1'",
                            ),
                            "Concentration": st.column_config.NumberColumn(
                                f"Concentration ({SS.conc_unit})",
                                format="%.5g",
                                help="Must be > 0 — used as log10(Concentration) in the fit.",
                            ),
                            "t_start": st.column_config.NumberColumn(
                                "t start (s)",
                                help="Start of the averaging window (seconds). Used only if Reading is blank.",
                            ),
                            "t_end": st.column_config.NumberColumn(
                                "t end (s)",
                                help="End of the averaging window (seconds). Used only if Reading is blank.",
                            ),
                            "avg_duration": st.column_config.NumberColumn(
                                "Avg window (s)",
                                format="%.4g",
                                help="If set, t start = t end − this value (overrides t start)",
                            ),
                            "Reading_mV": st.column_config.NumberColumn(
                                f"Reading ({SS.solid_unit})",
                                format="%.5g",
                                help="Optional direct entry — if filled, this IS the "
                                     "calibration point (no averaging window needed).",
                            ),
                        },
                    )
    
                    compute_clicked_solid = st.form_submit_button(
                        "Compute Calibration", type="primary")
    
                # Persist every edit immediately, same as Amperometry, so any
                # other code running later this pass sees the latest table.
                _active_frec_s["cpdf"] = _scpdf_edit
    
                def _do_compute_calibration_solid() -> bool:
                    """Nernstian (E vs log10 concentration) fit per channel — see
                    nernstian_lod_fit()'s docstring for why this differs from
                    Amperometry's linear ΔI model (no baseline subtraction, LOD
                    via independent-segment intersection, not 3·sigma/slope)."""
                    results = {}
                    for ch_name in analyze_chs_solid:
                        frec, ch = _solid_combo_lookup[ch_name]
                        cpdf = frec["cpdf"].copy()
                        if cpdf.empty:
                            continue
                        _rejected = cpdf["Concentration"].astype(float) <= 0
                        if _rejected.any():
                            st.warning(
                                f"**{ch_name}**: {int(_rejected.sum())} row(s) with "
                                "Concentration ≤ 0 excluded from the fit."
                            )
                            cpdf = cpdf[~_rejected].reset_index(drop=True)
                        if cpdf.empty:
                            st.error(f"**{ch_name}**: no valid calibration rows.")
                            continue
    
                        df = frec["df"]
                        t_arr = to_num(df[ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
                        e_arr = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
    
                        readings = []
                        for _, row in cpdf.iterrows():
                            if pd.notna(row.get("Reading_mV")):
                                readings.append(float(row["Reading_mV"]))
                                continue
                            _ets = _eff_t_start(row)
                            if _ets is None or pd.isna(row.get("t_end")):
                                readings.append(np.nan)
                                continue
                            mask = (t_arr >= _ets) & (t_arr <= row["t_end"])
                            pts  = e_arr[mask]
                            pts  = pts[~np.isnan(pts)]
                            readings.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
    
                        log_conc  = np.log10(cpdf["Concentration"].astype(float).to_numpy())
                        potential = np.array(readings, dtype=float)
                        valid     = ~np.isnan(potential)
                        if valid.sum() < 2:
                            st.error(
                                f"**{ch_name}**: fewer than 2 valid readings — fill "
                                "in Reading or a valid averaging window for more rows."
                            )
                            continue
    
                        lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
                        nernst_seg = lod_fit["nernstian_segment"]
                        ideal = nernst_ideal_slope_mv()
    
                        results[ch_name] = dict(
                            concs             = cpdf["Concentration"].astype(float).tolist(),
                            labels            = cpdf["Label"].tolist(),
                            log_conc          = log_conc[valid].tolist(),
                            potential_mv      = potential[valid].tolist(),
                            low_segment       = lod_fit["low_segment"],
                            nernstian_segment = nernst_seg,
                            lod_log10         = lod_fit["lod_log10"],
                            lod_conc          = lod_fit["lod_conc"],
                            sensitivity_mv_per_decade = nernst_seg["slope"] if nernst_seg else None,
                            pct_of_ideal_nernstian    = (
                                100.0 * abs(nernst_seg["slope"]) / ideal if nernst_seg else None
                            ),
                            ideal_slope_mv_per_decade = ideal,
                            is_average = False,
                        )
                    if not results:
                        SS.solid_cal_results = None
                        return False
                    SS.solid_cal_results = dict(results=results)
                    return True
    
                if compute_clicked_solid:
                    if not analyze_chs_solid:
                        st.error("Select at least one channel to analyse above.")
                    else:
                        SS["_solid_cal_computed_msg"] = (
                            "Calibration computed — results below."
                            if _do_compute_calibration_solid() else None
                        )
                if SS.get("_solid_cal_computed_msg"):
                    st.success(SS.pop("_solid_cal_computed_msg"))
    
                # ── Plot & statistics ───────────────────────────────────────────
                if SS.solid_cal_results:
                    res_map_s = SS.solid_cal_results["results"]
    
                    fig_solid = go.Figure()
                    stat_rows_s = []
                    for j, (ch_name, res) in enumerate(res_map_s.items()):
                        col = PAL[j % len(PAL)]
                        x = np.asarray(res["log_conc"], dtype=float)
                        y = np.asarray(res["potential_mv"], dtype=float)
                        labels_plot = res["labels"][:len(x)]
    
                        fig_solid.add_trace(go.Scatter(
                            x=x, y=y, name=ch_name, mode="markers+text",
                            text=labels_plot, textposition="top center",
                            textfont=dict(size=10),
                            marker=dict(color=col, size=10, symbol="circle",
                                        line=dict(width=1.5, color="white")),
                        ))
    
                        _low  = res.get("low_segment")
                        _nern = res.get("nernstian_segment")
                        _lod_log10 = res.get("lod_log10")
                        _has_lod = _lod_log10 is not None and np.isfinite(_lod_log10)
                        for _seg, _seg_name, _dash in [(_low, "low", "dot"), (_nern, "Nernstian", "dash")]:
                            if _seg is None:
                                continue
                            if _seg_name == "low" and _has_lod:
                                _x0, _x1 = float(np.min(x)), _lod_log10
                            elif _seg_name == "Nernstian" and _has_lod:
                                _x0, _x1 = _lod_log10, float(np.max(x))
                            else:
                                _x0, _x1 = float(np.min(x)), float(np.max(x))
                            if _x1 <= _x0:
                                _x0, _x1 = float(np.min(x)), float(np.max(x))
                            xp = np.linspace(_x0, _x1, 200)
                            yp = _seg["slope"] * xp + _seg["intercept"]
                            fig_solid.add_trace(go.Scatter(
                                x=xp, y=yp, name=f"{ch_name} {_seg_name} fit",
                                mode="lines", showlegend=False,
                                line=dict(color=col, dash=_dash, width=2),
                            ))
    
                        if _has_lod:
                            fig_solid.add_vline(
                                x=_lod_log10, line=dict(color=col, dash="dashdot", width=1.2),
                                annotation_text=f"{ch_name} LOD", annotation_position="top",
                            )
    
                        ideal = res.get("ideal_slope_mv_per_decade")
                        stat_rows_s.append({
                            "Channel": ch_name,
                            "Sensitivity (mV/decade)": fmt(res.get("sensitivity_mv_per_decade")),
                            "% of ideal Nernstian": (
                                f"{res['pct_of_ideal_nernstian']:.1f}%"
                                if res.get("pct_of_ideal_nernstian") is not None else "—"
                            ),
                            "Ideal (mV/decade)": fmt(ideal),
                            "R² (Nernstian)": (f"{_nern['r2']:.4f}" if _nern else "—"),
                            "R² (low)": (f"{_low['r2']:.4f}" if _low else "—"),
                            f"LOD ({SS.conc_unit})": fmt(res.get("lod_conc")),
                            "LOD (log₁₀)": fmt(res.get("lod_log10")),
                        })
    
                    _pt_s = _plot_theme()
                    fig_solid.update_layout(
                        xaxis_title=f"log₁₀(Concentration [{SS.conc_unit}])",
                        yaxis_title=f"Potential ({SS.solid_unit})",
                        hovermode="closest",
                        height=560,
                        template=_pt_s["template"],
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=True,
                        legend=dict(orientation="v", x=1.01, y=1, xanchor="left",
                                    yanchor="top", bgcolor="rgba(0,0,0,0)"),
                    )
                    st.plotly_chart(fig_solid, use_container_width=True,
                                    config={"displayModeBar": True,
                                            "modeBarButtonsToRemove": ["select2d", "lasso2d"]})
                    SS.solid_cal_fig = fig_solid
    
                    st.subheader("Statistics")
                    st.dataframe(pd.DataFrame(stat_rows_s), hide_index=True, use_container_width=True)
    
                    _render_ai_insights_section(res_map_s, "Nernstian", key_prefix="solid")
    
        # ═════════════════════════════════════════════════════════════════════════
        # TAB 4 · Export
        # ═════════════════════════════════════════════════════════════════════════
        with ST4:
            st.subheader("Export")
            st.caption("All exports are also available inline on the Time Series and Calibration Curve tabs.")
    
            if SS.solid_cal_results:
                st.markdown("#### Calibration summary table")
                rows_out_s = []
                cal_res_s: dict = SS.solid_cal_results["results"]
                for ch_name, res in cal_res_s.items():
                    for lbl, conc in zip(res["labels"], res["concs"]):
                        rows_out_s.append({
                            "Channel": ch_name,
                            "Label": lbl,
                            f"Concentration ({SS.conc_unit})": conc,
                        })
                export_df_s = pd.DataFrame(rows_out_s)
                st.dataframe(export_df_s, use_container_width=True, hide_index=True)
    
                st.markdown("#### Calibration curve downloads")
                sdl1, sdl2, sdl3 = st.columns(3)
                sdl1.download_button(
                    "Calibration CSV",
                    data=export_df_s.to_csv(index=False).encode(),
                    file_name="solid_state_calibration_data.csv",
                    mime="text/csv",
                )
                if SS.get("solid_cal_fig") is not None:
                    sdl2.download_button(
                        "Plot — interactive HTML",
                        data=SS.solid_cal_fig.to_html(include_plotlyjs="cdn"),
                        file_name="solid_state_calibration_curve.html",
                        mime="text/html",
                    )
                    solid_cal_png_bytes = render_solid_cal_png(
                        dict(cal_res_s), SS.conc_unit, SS.solid_unit,
                    )
                    sdl3.download_button(
                        "Plot — PNG (150 dpi)",
                        data=solid_cal_png_bytes,
                        file_name="solid_state_calibration_curve.png",
                        mime="image/png",
                    )
            else:
                st.info("Run calibration analysis in the **Calibration Curve** tab first.")
    
            if SS.solid_files:
                st.divider()
                st.markdown("#### Time-series downloads")
                sdl4, sdl5, sdl6 = st.columns(3)
                with sdl4:
                    for _fi4s, _frec_s in enumerate(SS.solid_files):
                        st.download_button(
                            f"Raw data CSV — {_frec_s['filename']}",
                            data=_frec_s["df"].to_csv(index=False).encode(),
                            file_name=(f"raw_{_frec_s['filename']}.csv"
                                       if not _frec_s["filename"].endswith(".csv")
                                       else f"raw_{_frec_s['filename']}"),
                            mime="text/csv",
                            key=f"solid_raw_dl_{_fi4s}_{_frec_s['filename']}",
                        )
                all_ch_names_export_s = [
                    _amp_label(f["filename"], c["name"], len(SS.solid_files) > 1)
                    for f in SS.solid_files for c in f["channels"]
                ]
                if SS.ts_fig is not None:
                    sdl5.download_button(
                        "Plot — interactive HTML",
                        data=SS.ts_fig.to_html(include_plotlyjs="cdn"),
                        file_name="solid_state_time_series.html",
                        mime="text/html",
                        key="solid_ts_html_dl",
                    )
                    ts_vis_s = SS.ts_visible if SS.ts_visible else all_ch_names_export_s
                    ts_png_bytes_s = render_ts_png(
                        SS.solid_files, SS.solid_unit, ts_vis_s,
                        smooth_method=SS.smooth_method,
                        smooth_window=SS.smooth_window,
                        smooth_polyorder=SS.smooth_polyorder,
                    )
                    sdl6.download_button(
                        "Plot — PNG (150 dpi)",
                        data=ts_png_bytes_s,
                        file_name="solid_state_time_series.png",
                        mime="image/png",
                        key="solid_ts_png_dl",
                    )
    
