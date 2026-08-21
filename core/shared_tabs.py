"""Import & Configure and Time Series tab bodies shared by Amperometry and
Solid-State (parameterized by files_key/unit_key — do not duplicate per
mode, see the refactor plan)."""

import io

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.constants import PAL, _plot_theme
from core.numeric import to_num, smooth_signal, _eff_t_start
from core.parsing import _parse_one_file
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style
from core.step_detection import detect_step_edges, edges_to_windows

SS = st.session_state


def _amp_label(filename: str, ch_name: str, multi: bool) -> str:
    """Composite (file, channel) label — bare channel name when only one file is loaded."""
    return f"{filename} · {ch_name}" if multi else ch_name


def render_ts_png(amp_files: list[dict], cur_unit: str, visible: list[str],
                  dpi: int = 150, fmt: str = "png",
                  figsize: tuple | None = None, style: str = "default",
                  smooth_method: str = "None", smooth_window: int = 11,
                  smooth_polyorder: int = 2) -> bytes:
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11   # axis label fontsize
    _lgfs = 7 if style == "minimal" else 9   # legend fontsize
    _afs = 7 if style == "minimal" else 8    # annotation fontsize
    _multi = len(amp_files) > 1
    _mpl_dashes = ["-", "--", ":", "-.", (0, (5, 1, 1, 1)), (0, (3, 1, 1, 1, 1, 1))]
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (13, 5))
        for fi, frec in enumerate(amp_files):
            for ci, ch in enumerate(frec["channels"]):
                lbl = _amp_label(frec["filename"], ch["name"], _multi)
                if lbl not in visible:
                    continue
                x   = to_num(frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
                _yr = to_num(frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                y   = smooth_signal(_yr, smooth_method, smooth_window, smooth_polyorder)
                _col = PAL[(fi if _multi else ci) % len(PAL)]
                _ls = _mpl_dashes[ci % len(_mpl_dashes)] if _multi else "-"
                if smooth_method != "None":
                    ax.plot(x, _yr, color=_col, linewidth=0.6, linestyle=_ls, alpha=0.30)
                ax.plot(x, y, color=_col, label=lbl, linewidth=1.4, linestyle=_ls)
        for frec in amp_files:
            for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
                _ets_png = _eff_t_start(row)
                if _ets_png is not None and pd.notna(row.get("t_end")):
                    clr = "darkorange" if row.get("Baseline") else "steelblue"
                    ax.axvspan(_ets_png, row["t_end"], alpha=0.10, color=clr)
                    ylim = ax.get_ylim()
                    _lbl_txt = (f"{frec['filename']}: {row['Label']}"
                                if _multi else str(row["Label"]))
                    ax.text(_ets_png + 0.5, ylim[1],
                            _lbl_txt, fontsize=_afs, va="top", color=clr)
        ax.set_xlabel("Time (s)", fontsize=_lfs)
        ax.set_ylabel(f"Current ({cur_unit})", fontsize=_lfs)
        ax.legend(fontsize=_lgfs, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_import_tab(
    files_key: str,
    signal_col_label: str,
    unit_key: str,
    active_file_key: str,
    seed_cpdf_fn,
    sample_loader_fn=None,
    sample_caption: str = "",
    sample_button_help: str = (
        "Loads a bundled example run with a pre-filled calibration "
        "table so you can try the app immediately without your own data."
    ),
    sample_loaded_msg: str = (
        "Sample data loaded — head to the **Time Series** or "
        "**Calibration Curve** tab to explore."
    ),
    sample_conc_unit: str | None = None,
    sample_signal_unit: str | None = None,
    set_legacy_alias: bool = False,
) -> None:
    """Shared 'Import & Configure' tab body — used by both Amperometry
    (files_key='amp_files', signal_col_label='Current', unit_key='cur_unit')
    and Solid-State (files_key='solid_files', signal_col_label='Potential',
    unit_key='solid_unit'). Upload, per-file channel mapping, and units are
    identical in shape between the two modes — only the starter calibration
    table (seed_cpdf_fn) and sample-data source differ."""
    with st.expander("Quick-start guide", expanded=False):
        st.markdown(f"""
**Typical workflow:**

1. **Import & Configure** — upload your CSV/TXT file, map each column pair (time + {signal_col_label.lower()}) to a named channel, set your concentration and {signal_col_label.lower()} units.
2. **Time Series** — inspect the raw traces. Use this to identify the time windows where each concentration was applied.
3. **Calibration Curve** — fill in the calibration table (one row per concentration step), click *Compute*, and review the statistics.
4. **Export** — download the calibration CSV, plots (PNG or interactive HTML), or the raw data.

> **Tip:** Calibration windows are shown as shaded bands on the time-series chart so you can visually verify your time entries.

> **Resuming previous work:** don't want to re-upload and re-map every time?
> The sidebar's **Configuration** section can save your settings and
> calibration table to this browser (**Save**), to a downloadable JSON file
> (**Export / Import JSON**) to move between machines, or — if your team has
> set it up — a full session including the raw uploaded files themselves to a
> shared Google Drive folder (**Cloud Sessions**), so anyone with access can
> pick up right where you left off.
""")

    if sample_loader_fn is not None:
        _sc1, _sc2 = st.columns([1, 3])
        if _sc1.button("Load sample data", key=f"{files_key}_load_sample", help=sample_button_help):
            _sample_files = sample_loader_fn()
            if _sample_files is None:
                st.error("Sample data files are missing from this deployment.")
            else:
                SS[files_key] = _sample_files
                if set_legacy_alias:
                    SS.df       = _sample_files[0]["df"]
                    SS.channels = _sample_files[0]["channels"]
                if sample_conc_unit is not None:
                    SS.conc_unit = sample_conc_unit
                if sample_signal_unit is not None:
                    SS[unit_key] = sample_signal_unit
                SS.ts_visible = []
                SS.cal_editor_version = SS.get("cal_editor_version", 0) + 1
                SS[active_file_key] = _sample_files[0]["filename"]
                SS["_files_applied_msg"] = sample_loaded_msg
                st.rerun()
        _sc2.caption(sample_caption)

    st.subheader("Upload File(s)")
    ups = st.file_uploader(
        "Drag and drop one or more raw sensor data files here, or click to browse",
        type=["csv", "txt", "pssession"],
        accept_multiple_files=True,
        key=f"{files_key}_uploader",
        help=(
            "Supports comma-, tab-, semicolon-, or space-delimited files with a header row. "
            "Upload multiple files to compare across runs/sensors — each file gets its own "
            "column mapping below."
        ),
    )

    if ups:
        _existing_by_name = {f["filename"]: f for f in SS[files_key]}
        _parsed_files = []
        for _fi, _up in enumerate(ups):
            with st.expander(f"📄 {_up.name}", expanded=(len(ups) <= 3)):
                try:
                    _df, _auto_channels = _parse_one_file(_up, _fi, key_prefix=files_key)
                except Exception as exc:
                    st.error(f"Parse error: {exc}")
                    continue

                m1, m2 = st.columns(2)
                m1.metric("Rows loaded", f"{len(_df):,}")
                m2.metric("Columns", len(_df.columns))
                st.dataframe(_df.head(10), use_container_width=True)

                st.markdown("**Map Columns to Channels**")
                _all_cols = list(_df.columns)
                _preset_chs = (
                    _existing_by_name[_up.name]["channels"]
                    if _up.name in _existing_by_name
                    else (_auto_channels or [])
                )
                _auto_n = len(_preset_chs) if _preset_chs else max(1, len(_all_cols) // 2)
                _n_ch = int(st.number_input(
                    "Number of channels", 1, 8,
                    value=min(8, _auto_n),
                    help="Each channel corresponds to one electrode. Most files have pairs of (time, signal) columns.",
                    key=f"{files_key}_n_ch_{_fi}",
                ))

                _ha, _hb, _hc = st.columns([2, 3, 3])
                _ha.markdown("**Channel name**")
                _hb.markdown("**Time column**")
                _hc.markdown(f"**{signal_col_label} column**")

                def _col_idx(col: str, _cols=_all_cols) -> int:
                    return _cols.index(col) if col in _cols else 0

                _new_chs = []
                for _i in range(_n_ch):
                    _preset = _preset_chs[_i] if _i < len(_preset_chs) else {}
                    _ca, _cb, _cc = st.columns([2, 3, 3])
                    _name = _ca.text_input(
                        "nm", _preset.get("name", f"Channel {_i + 1}"),
                        key=f"{files_key}_n{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _tc = _cb.selectbox(
                        "tc", _all_cols,
                        index=_col_idx(_preset.get("tc", _all_cols[min(_i * 2, len(_all_cols) - 1)])),
                        key=f"{files_key}_tc{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _ic = _cc.selectbox(
                        "ic", _all_cols,
                        index=_col_idx(_preset.get("ic", _all_cols[min(_i * 2 + 1, len(_all_cols) - 1)])),
                        key=f"{files_key}_ic{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _new_chs.append({"name": _name, "tc": _tc, "ic": _ic})

                _preset_cpdf = (
                    _existing_by_name[_up.name]["cpdf"]
                    if _up.name in _existing_by_name
                    else seed_cpdf_fn()
                )
                _parsed_files.append({
                    "filename": _up.name, "df": _df, "channels": _new_chs,
                    "cpdf": _preset_cpdf,
                })

        if _parsed_files and st.button("Apply Channel Configuration", type="primary", key=f"{files_key}_apply_cfg"):
            SS[files_key] = _parsed_files
            if set_legacy_alias:
                SS.df       = _parsed_files[0]["df"]
                SS.channels = _parsed_files[0]["channels"]
            SS.ts_visible = []
            SS.cal_editor_version = SS.get("cal_editor_version", 0) + 1
            SS["_files_applied_msg"] = (
                f"{len(_parsed_files)} file(s), "
                f"{sum(len(f['channels']) for f in _parsed_files)} channel(s) saved. "
                "Head to the **Time Series** tab to inspect your traces."
            )
            st.rerun()

    if SS.get("_files_applied_msg"):
        st.success(SS.pop("_files_applied_msg"))

    if SS[files_key]:
        st.divider()
        st.subheader("Units")
        st.caption("These labels appear on all plot axes and in the statistics table.")
        u1, u2 = st.columns(2)
        SS.conc_unit = u1.text_input("Concentration unit", SS.conc_unit,
                                      help="e.g. mM, µM, ppm, ng/mL",
                                      key="conc_unit_input")
        SS[unit_key] = u2.text_input(f"{signal_col_label} unit", SS[unit_key],
                                      help="Shown on plot axes and in the statistics table",
                                      key=f"{files_key}_signal_unit_input")


_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]


def _render_timeseries_tab(files_key: str, unit_key: str, signal_axis_label: str) -> None:
    """Shared 'Time Series' tab body — generic over which files list /
    signal-unit setting to read from SS. Amperometry and Solid-State share
    this verbatim; only the y-axis label and file source differ."""
    _files = SS[files_key]
    if not _files:
        st.info("Complete the **Import & Configure** step first.")
        return

    _multi_file = len(_files) > 1

    st.caption(
        "Use this chart to identify the time windows for each concentration step. "
        "Shaded bands show the averaging windows defined in the **Calibration Curve** tab — "
        "orange for the baseline, blue for analyte steps."
    )

    with st.expander("Signal smoothing", expanded=False):
        st.caption(
            "Optional — smooths the trace shown below and the signal used for the "
            "calibration averaging windows in the **Calibration Curve** tab. Off by default."
        )
        sm1, sm2, sm3 = st.columns(3)
        SS.smooth_method = sm1.selectbox(
            "Method", ["None", "Moving average", "Savitzky-Golay"],
            index=["None", "Moving average", "Savitzky-Golay"].index(SS.smooth_method),
            key=f"{files_key}_smooth_method",
        )
        if SS.smooth_method != "None":
            SS.smooth_window = int(sm2.number_input(
                "Window (samples)", min_value=3, value=int(SS.smooth_window), step=2,
                help="Odd number of samples in the smoothing window.",
                key=f"{files_key}_smooth_window",
            ))
            if SS.smooth_method == "Savitzky-Golay":
                SS.smooth_polyorder = int(sm3.number_input(
                    "Polynomial order", min_value=1, max_value=5,
                    value=int(SS.smooth_polyorder),
                    help="Must be less than the window size.",
                    key=f"{files_key}_smooth_polyorder",
                ))

    _combos = [
        (fi, ci, frec["filename"], frec["df"], ch)
        for fi, frec in enumerate(_files)
        for ci, ch in enumerate(frec["channels"])
    ]
    _all_ch_names = [_amp_label(fn, ch["name"], _multi_file) for _, _, fn, _, ch in _combos]
    _vis_key = f"{files_key}_ts_vis_ms"
    if _vis_key not in SS or any(c not in _all_ch_names for c in SS.get(_vis_key, [])):
        SS[_vis_key] = _all_ch_names[:]

    if len(_combos) >= 2:
        _iso_cols = st.columns([1.4] + [1] * len(_combos))
        _iso_cols[0].markdown("**Isolate:**", help="Click a name to show only that trace")
        for _j, _lbl in enumerate(_all_ch_names):
            if _iso_cols[_j + 1].button(
                _lbl, key=f"{files_key}_ts_solo_{_j}",
                use_container_width=True,
                help=f"Show only {_lbl}",
            ):
                SS[_vis_key] = [_lbl]

    sel = st.multiselect("Visible channels", _all_ch_names, key=_vis_key)
    SS.ts_visible = sel

    with st.expander("Y-axis range", expanded=False):
        _y_auto = st.checkbox("Auto-scale", value=SS.ts_y_auto, key=f"{files_key}_ts_y_auto_cb")
        SS.ts_y_auto = _y_auto
        if not _y_auto:
            _ts_all_y: list[float] = []
            for _fi2, _ci2, _fn2, _df2, _ch2 in _combos:
                _lbl2 = _amp_label(_fn2, _ch2["name"], _multi_file)
                if _lbl2 not in sel:
                    continue
                _yr2 = to_num(_df2[_ch2["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                _ts_all_y.extend(_yr2[np.isfinite(_yr2)].tolist())
            _dr_lo = float(np.nanmin(_ts_all_y)) if _ts_all_y else 0.0
            _dr_hi = float(np.nanmax(_ts_all_y)) if _ts_all_y else 1.0
            _def_min = SS.ts_y_min if SS.ts_y_min is not None else _dr_lo
            _def_max = SS.ts_y_max if SS.ts_y_max is not None else _dr_hi
            _yc1, _yc2 = st.columns(2)
            _range_help = f"Full visible-channel range: {_dr_lo:.4g} – {_dr_hi:.4g}"
            SS.ts_y_min = float(_yc1.number_input(
                "Y min", value=float(_def_min), format="%.6g", step=0.0001,
                key=f"{files_key}_ts_y_min_ni", help=_range_help,
            ))
            SS.ts_y_max = float(_yc2.number_input(
                "Y max", value=float(_def_max), format="%.6g", step=0.0001,
                key=f"{files_key}_ts_y_max_ni", help=_range_help,
            ))

    fig_ts = go.Figure()
    for fi, ci, fn, df, ch in _combos:
        lbl = _amp_label(fn, ch["name"], _multi_file)
        if lbl not in sel:
            continue
        _t = to_num(df[ch["tc"]])
        _i_raw = to_num(df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
        _i_smooth = smooth_signal(_i_raw, SS.smooth_method, SS.smooth_window, SS.smooth_polyorder)
        _col = PAL[(fi if _multi_file else ci) % len(PAL)]
        _dash = _DASHES[ci % len(_DASHES)] if _multi_file else "solid"
        if SS.smooth_method != "None":
            fig_ts.add_trace(go.Scatter(
                x=_t, y=_i_raw,
                name=f"{lbl} (raw)",
                mode="lines",
                opacity=0.35,
                line=dict(color=_col, width=1, dash=_dash),
                showlegend=False,
            ))
        fig_ts.add_trace(go.Scatter(
            x=_t,
            y=_i_smooth,
            name=lbl,
            mode="lines",
            line=dict(color=_col, width=1.5, dash=_dash),
        ))

    _pt_ts = _plot_theme()
    for _frec_sh in _files:
        for _, row in _frec_sh.get("cpdf", pd.DataFrame()).iterrows():
            _ets2 = _eff_t_start(row)
            if _ets2 is not None and pd.notna(row.get("t_end")):
                clr = ("rgba(255,165,0,0.22)"
                       if row.get("Baseline") else "rgba(100,160,255,0.15)")
                _lbl_sh = (f"{_frec_sh['filename']}: {row['Label']}"
                           if _multi_file else str(row["Label"]))
                fig_ts.add_vrect(
                    x0=_ets2, x1=row["t_end"],
                    fillcolor=clr, layer="below", line_width=0,
                    annotation_text=_lbl_sh,
                    annotation_position="top left",
                    annotation=dict(font_size=10, font_color=_pt_ts["annot_font"]),
                )

    # Candidate step edges from "Auto-detect step times" (core.step_detection),
    # not yet applied to the calibration table — shown as thin dotted lines,
    # distinct from the shaded calibration windows above, so the user can
    # visually confirm them before clicking Apply.
    _autodetect_edges = SS.get(f"{files_key}_autodetect_edges", {})
    for _frec_sh in _files:
        for _edge in _autodetect_edges.get(_frec_sh["filename"], []):
            fig_ts.add_vline(
                x=_edge, line_dash="dot", line_color="#e91e63", opacity=0.55,
                annotation_text="detected", annotation_position="bottom",
                annotation=dict(font_size=9, font_color="#e91e63"),
            )

    fig_ts.update_layout(
        xaxis_title="Time (s)",
        yaxis_title=f"{signal_axis_label} ({SS[unit_key]})",
        hovermode="x unified",
        height=580,
        template=_pt_ts["template"],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(
            orientation="v", x=1.01, y=1,
            xanchor="left", yanchor="top",
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverdistance=40,
        xaxis=dict(
            rangeslider=dict(visible=True, thickness=0.05,
                             bgcolor="rgba(255,255,255,0.05)"),
            showspikes=True, spikemode="across", spikesnap="cursor",
            spikecolor=_pt_ts["spike"], spikethickness=1, spikedash="dot",
            showgrid=True, gridcolor=_pt_ts["grid"],
            linecolor=_pt_ts["axisline"],
        ),
        yaxis=dict(
            showspikes=True, spikemode="across",
            spikecolor=_pt_ts["spike"], spikethickness=1, spikedash="dot",
            showgrid=True, gridcolor=_pt_ts["grid"],
            linecolor=_pt_ts["axisline"],
            fixedrange=False,
            **({"range": [SS.ts_y_min, SS.ts_y_max]}
               if not SS.ts_y_auto and SS.ts_y_min is not None and SS.ts_y_max is not None
               else {}),
        ),
    )
    st.plotly_chart(fig_ts, use_container_width=True,
                    config={"scrollZoom": True, "displayModeBar": True,
                            "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
                    key=f"{files_key}_ts_chart")
    SS.ts_fig = fig_ts

    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "Download as interactive HTML",
        data=fig_ts.to_html(include_plotlyjs="cdn"),
        file_name="time_series.html",
        mime="text/html",
        key=f"{files_key}_ts_html_dl",
    )
    ts_png = render_ts_png(_files, SS[unit_key], sel,
                           smooth_method=SS.smooth_method,
                           smooth_window=SS.smooth_window,
                           smooth_polyorder=SS.smooth_polyorder)
    dl2.download_button(
        "Download as PNG",
        data=ts_png,
        file_name="time_series.png",
        mime="image/png",
        key=f"{files_key}_ts_png_dl",
    )


def _render_autodetect_expander(
    files_key: str,
    active_frec: dict,
    build_cpdf_fn,
    key_prefix: str,
    has_baseline: bool = False,
) -> None:
    """Shared 'Auto-detect step times from trace' control, used by both
    Amperometry and Solid-State's Calibration Curve tabs (see
    _preset_cpdf_amp / _preset_cpdf_solid for the arithmetic-preset
    equivalent this complements). Detects candidate step-transition times
    from the active file's own trace via core.step_detection, previews them
    as dotted lines on the Time Series chart (added in
    _render_timeseries_tab above), and — only once the user clicks Apply —
    rebuilds the calibration table via build_cpdf_fn(windows), which turns
    the generic (label, t_start, t_end) triples into that mode's full
    column schema (Amp adds Spike Vol/Stock Conc/Baseline; Solid adds
    Reading_mV)."""
    channels = active_frec.get("channels", [])
    if not channels:
        return
    with st.expander("Auto-detect step times from trace"):
        st.caption(
            "Finds abrupt jumps in the trace — the moments a new "
            "concentration was likely added — and turns them into "
            "t start / t end windows, instead of assuming even spacing "
            "like the preset above. Useful when a run's spikes weren't "
            "added on a perfectly regular schedule."
        )
        ch_names = [c["name"] for c in channels]
        d1, d2, d3 = st.columns(3)
        ch_pick = d1.selectbox(
            "Channel to analyse", ch_names, key=f"{key_prefix}_autodetect_ch",
            help="Detection runs on one representative channel — the "
                 "resulting windows apply to every channel in this file.",
        )
        sensitivity = d2.slider(
            "Sensitivity", 0.3, 3.0, 1.0, step=0.1,
            key=f"{key_prefix}_autodetect_sensitivity",
            help="Lower = more sensitive (finds smaller/noisier steps, "
                 "but may over-detect). Higher = stricter.",
        )
        min_gap = d3.number_input(
            "Min. seconds between steps", min_value=1.0, value=30.0,
            format="%.5g", key=f"{key_prefix}_autodetect_min_gap",
            help="Candidate edges closer together than this are merged — "
                 "set below your shortest expected step duration.",
        )
        e1, e2 = st.columns(2)
        max_steps = e1.number_input(
            "Expected step count (optional)", min_value=0, value=0,
            help="If set, keeps only the N most prominent edges. Leave at "
                 "0 to keep every candidate found.",
            key=f"{key_prefix}_autodetect_max_steps",
        )
        include_baseline = (
            e2.checkbox(
                "Leading Baseline row (0 → first step)", value=True,
                key=f"{key_prefix}_autodetect_baseline",
            )
            if has_baseline else False
        )

        edges_key = f"{files_key}_autodetect_edges"
        if st.button("Detect", key=f"{key_prefix}_autodetect_run"):
            ch = next(c for c in channels if c["name"] == ch_pick)
            t_arr = to_num(active_frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
            i_arr = to_num(active_frec["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            i_arr = smooth_signal(i_arr, SS.smooth_method, SS.smooth_window, SS.smooth_polyorder)
            edges = detect_step_edges(
                t_arr, i_arr,
                min_step_seconds=float(min_gap),
                sensitivity=float(sensitivity),
                max_edges=int(max_steps) or None,
            )
            SS.setdefault(edges_key, {})[active_frec["filename"]] = edges
            if not edges:
                st.warning(
                    "No clear step transitions found — try lowering "
                    "sensitivity, or this trace may be flat/too noisy."
                )
            else:
                st.success(
                    f"Found {len(edges)} candidate step time(s) — check the "
                    "**Time Series** tab (dotted magenta lines) to confirm "
                    "before applying."
                )
                # Time Series is defined earlier in this tab's script than
                # Calibration Curve, so within THIS run it already rendered
                # before these edges existed — rerun now so it picks them up
                # before the user switches tabs (st.tabs() doesn't itself
                # trigger a rerun on click).
                st.rerun()

        edges = SS.get(edges_key, {}).get(active_frec["filename"], [])
        if edges:
            st.caption("Detected times (s): " + ", ".join(f"{e:.4g}" for e in edges))
            if st.button("Apply detected edges — replaces the table below",
                         key=f"{key_prefix}_autodetect_apply"):
                ch = next(c for c in channels if c["name"] == ch_pick)
                t_arr = to_num(active_frec["df"][ch["tc"]]).to_numpy(dtype=float, na_value=np.nan)
                trace_end = float(np.nanmax(t_arr)) if t_arr.size else 0.0
                windows = edges_to_windows(edges, trace_end, include_baseline)
                active_frec["cpdf"] = build_cpdf_fn(windows)
                SS.cal_editor_version = SS.get("cal_editor_version", 0) + 1
                st.success(f"Applied {len(windows)} row(s) from detected edges.")
                st.rerun()
