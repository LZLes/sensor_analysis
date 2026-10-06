"""Solid-State (potentiometric ISE/ISFET) mode: Nernstian E vs log10(Concentration)
calibration — no Baseline/blank-subtraction, no dilution calculator (unlike Amperometry)."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ai_insights import _render_ai_insights_section
from core.calibration_table import _default_solid_cpdf
from core.constants import PAL, _plot_theme, fmt
from core.numeric import to_num, _eff_t_start
from core.shared_tabs import (
    _amp_label, _render_autodetect_expander, _render_import_tab,
    _render_timeseries_tab, render_ts_png,
)
from core.analysis.solid_state import (  # noqa: F401  (re-exported)
    _FARADAY_F, _GAS_CONSTANT_R, _MV_PER_SIGNAL_UNIT, _SOLID_PRESETS, _SOLID_SAMPLE_FILES,
    _SOLID_SAMPLE_STEPS, _cpdf_from_autodetect_windows, _load_solid_sample_data, _preset_cpdf_solid,
    _solid_sample_cpdf, ideal_slope_in_signal_unit, nernst_ideal_slope_mv, nernstian_lod_fit,
    render_solid_cal_png,
)

SS = st.session_state


def _seed_solid_cpdf_for_new_file() -> pd.DataFrame:
    """Starter calibration table for a newly-imported solid-state file."""
    return _default_solid_cpdf()


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
                conc_unit_key="solid_conc_unit",
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
                _nz1, _nz2 = st.columns(2)
                _ion_charge = int(_nz1.number_input(
                    "Ion charge |z|", min_value=1, max_value=4, value=1, step=1,
                    key="solid_ion_charge",
                    help="Charge of the analyte ion — 1 for Na+/K+/Cl-, 2 for "
                         "Ca2+/Mg2+, etc. Used only for the ideal-Nernstian-slope "
                         "comparison below (does not affect the fit itself).",
                ))
                _lab_temp_c = float(_nz2.number_input(
                    "Lab temperature (°C)", min_value=-20.0, max_value=100.0,
                    value=25.0, step=0.5, key="solid_lab_temp_c",
                    help="Ideal Nernstian slope depends on temperature — set this "
                         "to the actual temperature the run was measured at.",
                ))
    
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
                        f"Concentration values ({SS.solid_conc_unit}), comma-separated — absolute, "
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
                            SS.solid_files_cal_editor_version = SS.get("solid_files_cal_editor_version", 0) + 1
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
                if "solid_files_cal_editor_version" not in SS:
                    SS.solid_files_cal_editor_version = 0
    
                # Only the editor + Compute button share the form now — so the
                # button's rerun reads the grid's live value at submit time,
                # rather than depending on a separate blur event racing the
                # click — while channel selection above stays reactive.
                with st.form(key=f"solid_cal_form_{_active_fi_s}"):
                    _scpdf_edit = st.data_editor(
                        _active_frec_s["cpdf"],
                        key=f"solid_cal_editor_{_active_fi_s}_{SS.solid_files_cal_editor_version}",
                        num_rows="dynamic",
                        use_container_width=True,
                        column_config={
                            "Label": st.column_config.TextColumn(
                                "Label",
                                help="Short name shown on the plot, e.g. 'Std 1'",
                            ),
                            "Concentration": st.column_config.NumberColumn(
                                f"Concentration ({SS.solid_conc_unit})",
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
                            st.warning(f"**{ch_name}**: calibration table is empty — "
                                       f"add rows in {frec['filename']}'s table above.")
                            continue
                        # ~(x > 0) catches both x <= 0 AND NaN (NaN > 0 is False),
                        # matching the caption's "must be > 0" promise — a plain
                        # `<= 0` comparison would let a blank/NaN cell slip through.
                        _rejected = ~(cpdf["Concentration"].astype(float) > 0)
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
                        # Ideal slope converted into the ACTUAL configured signal
                        # unit — nernst_ideal_slope_mv() alone is always in mV,
                        # but the fitted slope is in whatever solid_unit the user
                        # set (e.g. "V"), so comparing raw mV to raw fitted slope
                        # silently corrupts "% of ideal" if unit != mV.
                        ideal = ideal_slope_in_signal_unit(_lab_temp_c, _ion_charge, SS.solid_unit)

                        results[ch_name] = dict(
                            concs             = cpdf["Concentration"].astype(float).tolist(),
                            labels            = cpdf["Label"].tolist(),
                            valid_mask        = valid.tolist(),
                            log_conc          = log_conc[valid].tolist(),
                            potential_mv      = potential[valid].tolist(),
                            low_segment       = lod_fit["low_segment"],
                            nernstian_segment = nernst_seg,
                            lod_log10         = lod_fit["lod_log10"],
                            lod_conc          = lod_fit["lod_conc"],
                            sensitivity_mv_per_decade = nernst_seg["slope"] if nernst_seg else None,
                            pct_of_ideal_nernstian    = (
                                100.0 * abs(nernst_seg["slope"]) / ideal
                                if (nernst_seg and ideal) else None
                            ),
                            ideal_slope_mv_per_decade = ideal,
                            signal_unit = SS.solid_unit,
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
                        # Index labels by the same valid_mask used to filter
                        # log_conc/potential_mv above — a plain length-based
                        # truncation would mislabel every point after the first
                        # invalid (NaN-reading) row with the wrong standard's name.
                        _vmask = res.get("valid_mask", [True] * len(res["labels"]))
                        labels_plot = np.asarray(res["labels"], dtype=object)[_vmask]
    
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
                        _sunit = res.get("signal_unit", SS.solid_unit)
                        stat_rows_s.append({
                            "Channel": ch_name,
                            f"Sensitivity ({_sunit}/decade)": fmt(res.get("sensitivity_mv_per_decade")),
                            "% of ideal Nernstian": (
                                f"{res['pct_of_ideal_nernstian']:.1f}%"
                                if res.get("pct_of_ideal_nernstian") is not None
                                else ("—" if ideal is not None else "unit not recognized")
                            ),
                            f"Ideal ({_sunit}/decade)": fmt(ideal),
                            "R² (Nernstian)": (f"{_nern['r2']:.4f}" if _nern else "—"),
                            "R² (low)": (f"{_low['r2']:.4f}" if _low else "—"),
                            f"LOD ({SS.solid_conc_unit})": fmt(res.get("lod_conc")),
                            "LOD (log₁₀)": fmt(res.get("lod_log10")),
                        })
    
                    _pt_s = _plot_theme()
                    fig_solid.update_layout(
                        xaxis_title=f"log₁₀(Concentration [{SS.solid_conc_unit}])",
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
                    st.plotly_chart(fig_solid, use_container_width=True, key="solid_cal_chart",
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
                            f"Concentration ({SS.solid_conc_unit})": conc,
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
                        dict(cal_res_s), SS.solid_conc_unit, SS.solid_unit,
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
                _solid_ts_fig = SS.get("solid_files_ts_fig")
                if _solid_ts_fig is not None:
                    sdl5.download_button(
                        "Plot — interactive HTML",
                        data=_solid_ts_fig.to_html(include_plotlyjs="cdn"),
                        file_name="solid_state_time_series.html",
                        mime="text/html",
                        key="solid_ts_html_dl",
                    )
                    ts_vis_s = SS.get("solid_files_ts_vis_ms") or all_ch_names_export_s
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
    
