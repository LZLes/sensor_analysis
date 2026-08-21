"""Assay (microplate / 4PL) mode: import, standards, standard curve, results."""

import io

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.constants import PAL, _MIME, _plot_theme, fmt
from core.numeric import lin_reg
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style

SS = st.session_state


_PLATE_ROWS = list("ABCDEFGH")


def _well_rc(well: str) -> tuple[int, int] | None:
    """'A1' → (0, 0), 'H12' → (7, 11). None if invalid."""
    w = well.strip().upper()
    if not w or w[0] not in _PLATE_ROWS:
        return None
    try:
        c = int(w[1:]) - 1
    except ValueError:
        return None
    return (_PLATE_ROWS.index(w[0]), c) if 0 <= c < 12 else None


def _plate_get(plate_df: pd.DataFrame | None, well: str) -> float:
    rc = _well_rc(well)
    if rc is None or plate_df is None:
        return np.nan
    try:
        return float(plate_df.iat[rc[0], rc[1]])  # type: ignore[arg-type]
    except Exception:
        return np.nan


def parse_plate_csv(raw: str) -> pd.DataFrame:
    """
    Parse a microplate reader export into an 8×12 DataFrame (index A–H, cols 1–12).
    Handles TECAN/Synergy/generic grid formats (tab, comma, semicolon delimited).
    """
    import re as _re
    row_re = _re.compile(r'^\s*([A-Ha-h])(?:[,;\t]|\s)')
    grid: dict[str, list[float]] = {}
    for line in raw.splitlines():
        m = row_re.match(line)
        if not m:
            continue
        letter = m.group(1).upper()
        parts  = _re.split(r'[,;\t]+', line.strip())
        if len(parts) < 2:
            parts = line.strip().split()
        nums: list[float] = []
        for p in parts[1:]:
            try:
                nums.append(float(p.strip().replace(",", ".")))
            except ValueError:
                continue
        if nums:
            grid[letter] = nums[:12]
    if not grid:
        raise ValueError(
            "No plate rows found — expected rows labeled A–H. "
            "Check the file has a standard grid layout."
        )
    data = {}
    for r in _PLATE_ROWS:
        row_vals = (grid.get(r, []) + [np.nan] * 12)[:12]
        data[r] = row_vals
    df = pd.DataFrame(data, index=range(1, 13)).T
    df.index   = pd.Index(_PLATE_ROWS, name="Row")
    df.columns = pd.Index(range(1, 13), name="Col")
    return df


def _plate_fig(plate_df: pd.DataFrame | None, std_wells: dict,
               sample_map: dict, conc_unit: str, sig_unit: str) -> go.Figure:
    """
    Interactive 96-well plate map.
    std_wells : {well_str: {set, conc, label, is_blank}}
    sample_map: {well_str: label}
    """
    _SET_COLS = {
        1: "rgba(70,130,220,0.85)",
        2: "rgba(50,200,120,0.85)",
        3: "rgba(220,80,80,0.85)",
    }
    _BLANK_COL  = "rgba(255,152,0,0.90)"
    _SAMPLE_COL = "rgba(160,100,220,0.75)"
    _EMPTY_COL  = "rgba(80,80,80,0.35)"

    xs, ys, txts, hovs, cols = [], [], [], [], []
    for ri, row_lbl in enumerate(_PLATE_ROWS):
        for ci in range(12):
            well  = f"{row_lbl}{ci + 1}"
            val   = _plate_get(plate_df, well)
            val_s = f"{val:.4g}" if np.isfinite(val) else "—"
            if well in std_wells:
                info = std_wells[well]
                col  = _BLANK_COL if info["is_blank"] else _SET_COLS.get(info["set"], _SET_COLS[1])
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}<br>"
                            f"Std: {info['label']}  ({info['conc']} {conc_unit})"
                            f"<br>Set {info['set']}")
            elif well in sample_map:
                col = _SAMPLE_COL
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}<br>"
                            f"Sample: {sample_map[well]}")
            else:
                col = _EMPTY_COL
                hovs.append(f"<b>{well}</b><br>Signal: {val_s} {sig_unit}")
            xs.append(ci + 1)
            ys.append(7 - ri)
            txts.append(val_s if np.isfinite(val) else "")
            cols.append(col)

    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="markers+text",
        text=txts, textposition="middle center",
        textfont=dict(size=6.5, color="rgba(255,255,255,0.92)"),
        hovertext=hovs, hoverinfo="text",
        marker=dict(color=cols, size=30, symbol="circle",
                    line=dict(width=0.5, color="rgba(255,255,255,0.15)")),
        showlegend=False,
    ))
    for _ltxt, _lcol in [
        ("Blank", _BLANK_COL), ("Set 1", _SET_COLS[1]),
        ("Set 2", _SET_COLS[2]), ("Set 3", _SET_COLS[3]),
        ("Sample", _SAMPLE_COL), ("—", _EMPTY_COL),
    ]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(color=_lcol, size=10, symbol="circle"),
            name=_ltxt, showlegend=True,
        ))
    _pt = _plot_theme()
    fig.update_layout(
        height=345, template=_pt["template"],
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(tickmode="array", tickvals=list(range(1, 13)),
                   ticktext=[str(i) for i in range(1, 13)],
                   showgrid=False, zeroline=False, range=[0.3, 12.7], title=""),
        yaxis=dict(tickmode="array", tickvals=list(range(8)),
                   ticktext=list(reversed(_PLATE_ROWS)),
                   showgrid=False, zeroline=False, range=[-0.5, 7.5], title=""),
        legend=dict(orientation="h", x=0, y=-0.12, xanchor="left", font=dict(size=9)),
        margin=dict(l=35, r=15, t=15, b=50),
        hoverlabel=dict(bgcolor="rgba(30,30,30,0.92)"),
    )
    return fig


def _fit_4pl(x: np.ndarray, y: np.ndarray) -> dict | None:
    """4-parameter logistic: y = d + (a − d) / (1 + (x/c)^b)."""
    from scipy.optimize import curve_fit as _cf

    def _model(xv, a, b, c, d):
        return d + (a - d) / (1.0 + (np.asarray(xv) / c) ** b)

    xpos = x[x > 0]
    c0 = float(np.median(xpos)) if xpos.size else 1.0
    try:
        popt, _ = _cf(_model, x, y,
                       p0=[float(y.min()), 1.0, c0, float(y.max())],
                       maxfev=10000,
                       bounds=([-np.inf, 0.01, 1e-12, -np.inf],
                               [ np.inf, 10.0,  np.inf,  np.inf]))
        yp = _model(x, *popt)
        ss_res = float(np.sum((y - yp) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return dict(type="4pl", a=popt[0], b=popt[1], c=popt[2], d=popt[3], r2=r2)
    except Exception:
        return None


def _4pl_inv(y_val: float, p: dict) -> float:
    a, b, c, d = p["a"], p["b"], p["c"], p["d"]
    try:
        ratio = (a - d) / (float(y_val) - d)
        return float(c * (ratio - 1.0) ** (1.0 / b)) if ratio > 0 else np.nan
    except Exception:
        return np.nan


def render_assay_curve(res: dict, show_reps: bool, conc_unit: str, sig_unit: str,
                       dpi: int = 150, fmt: str = "png",
                       figsize: tuple | None = None, style: str = "default") -> bytes:
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11
    fit  = res["fit"]
    cx   = np.array(res["concs"], float)
    my   = np.array(res["means"], float)
    sy   = np.array(res["sds"],   float)
    darr = np.array(res["delta_arr"], float)
    vm   = np.isfinite(my) & np.isfinite(cx)
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (7, 5))
        if show_reps:
            for si, rc in enumerate([PAL[0], PAL[1], PAL[2]]):
                ry = darr[:, si]
                vr = np.isfinite(ry) & np.isfinite(cx)
                if vr.any():
                    ax.scatter(cx[vr], ry[vr], color=rc, s=22, alpha=0.6,
                               marker="o", facecolors="none", linewidths=1.2,
                               zorder=3, label=f"Set {si + 1}")
        ax.errorbar(cx[vm], my[vm], yerr=sy[vm], fmt="o", color="#4c96d7",
                    capsize=4, markersize=7, linewidth=1.4, elinewidth=1.2,
                    zorder=4, label="Mean")
        xp = np.linspace(max(0.0, cx[vm].min()), cx[vm].max(), 400)
        if fit["type"] == "linear":
            yp   = fit["slope"] * xp + fit["intercept"]
            b    = fit["intercept"]
            _eq  = (f"y = {fit['slope']:.3g}x {'+ ' if b >= 0 else '− '}{abs(b):.3g}"
                    f"\nR² = {fit['r2']:.4f}")
        elif fit["type"] == "quad":
            yp  = fit["a"]*xp**2 + fit["b"]*xp + fit["c"]
            _eq = (f"y = {fit['a']:.3g}x² + {fit['b']:.3g}x + {fit['c']:.3g}"
                   f"\nR² = {fit['r2']:.4f}")
        else:
            yp  = fit["d"] + (fit["a"] - fit["d"]) / (1 + (xp / fit["c"]) ** fit["b"])
            _eq = (f"4PL  a={fit['a']:.3g}  b={fit['b']:.3g}\n"
                   f"c={fit['c']:.3g}  d={fit['d']:.3g}  R²={fit['r2']:.4f}")
        ax.plot(xp, yp, "--", color="#ff9230", linewidth=2, label="Fit")
        ax.set_xlabel(f"Concentration ({conc_unit})", fontsize=_lfs)
        ax.set_ylabel(f"ΔSignal ({sig_unit})", fontsize=_lfs)
        ax.legend(fontsize=7, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        ax.text(0.5, -0.22, _eq, transform=ax.transAxes, fontsize=7,
                va="top", ha="center", family="monospace",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          alpha=0.88, edgecolor="#cccccc", linewidth=0.8))
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render() -> None:
        AS1, AS2, AS3, AS4 = st.tabs([
            "① Import", "② Standards", "③ Standard Curve", "④ Results & Export",
        ])
    
        # ── helpers shared across tabs ────────────────────────────────────────────
        def _build_std_wells_map() -> dict:
            """Build {well_str: {set, conc, label, is_blank}} from assay_std_df."""
            m: dict = {}
            for _i, _r in SS["assay_std_df"].iterrows():
                _is_bl = (_i == 0)
                for _s_idx, _s_col in [(1, "S1"), (2, "S2"), (3, "S3")]:
                    _w = str(_r.get(_s_col, "")).strip().upper()
                    if _w and _well_rc(_w):
                        m[_w] = {"set": _s_idx, "conc": float(_r.get("Conc", 0)),
                                  "label": str(_r.get("Label", "")), "is_blank": _is_bl}
            return m
    
        def _build_sample_map() -> dict:
            m: dict = {}
            for _, _r in SS["assay_sample_df"].iterrows():
                _w = str(_r.get("Well", "")).strip().upper()
                if _w:
                    m[_w] = str(_r.get("Label", _w))
            return m
    
        # ── AS1 · Import ──────────────────────────────────────────────────────────
        with AS1:
            st.subheader("Import Plate Data")
            _a1c1, _a1c2 = st.columns(2)
            SS["assay_sig_unit"]  = _a1c1.text_input(
                "Signal unit", SS["assay_sig_unit"], help="e.g. Abs, RFU, RLU")
            SS["assay_conc_unit"] = _a1c2.text_input(
                "Concentration unit", SS["assay_conc_unit"], help="e.g. µM, nM, mg/L")
    
            _a1_up = st.file_uploader(
                "Plate reader file (CSV / TXT)",
                type=["csv", "txt"], key="assay_up",
                help="Grid format with rows A–H. TECAN, Synergy, and generic tab/comma files are supported.",
            )
            if _a1_up is not None:
                try:
                    SS["assay_plate"] = parse_plate_csv(
                        _a1_up.read().decode("utf-8", errors="replace")
                    )
                    st.success(f"Loaded — {int(SS['assay_plate'].notna().sum().sum())} wells with data.")
                except Exception as _exc_a1:
                    st.error(f"Parse error: {_exc_a1}")
    
            st.divider()
            st.subheader("Manual entry")
            st.caption("Edit the grid directly — rows A–H, columns 1–12.")
            _a1_init = (SS["assay_plate"] if SS["assay_plate"] is not None else
                        pd.DataFrame(np.full((8, 12), np.nan),
                                     index=pd.Index(_PLATE_ROWS, name="Row"),
                                     columns=pd.Index(range(1, 13), name="Col")))
            # Editor + Apply button share one st.form so the button's rerun
            # reads the grid's live value at submit time, instead of depending
            # on a separate blur event racing the click.
            with st.form(key="assay_plate_form"):
                _a1_edited = st.data_editor(
                    _a1_init.reset_index(),
                    key="assay_plate_editor",
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Row": st.column_config.TextColumn("Row", disabled=True)},
                )
                _a1_apply_clicked = st.form_submit_button("Apply manual values")
            if _a1_apply_clicked:
                _mdf = _a1_edited.copy()
                if "Row" in _mdf.columns:
                    _mdf = _mdf.set_index("Row")
                _mdf.index   = pd.Index(_PLATE_ROWS[:len(_mdf)], name="Row")
                _mdf.columns = pd.Index(range(1, len(_mdf.columns) + 1), name="Col")
                SS["assay_plate"] = _mdf.apply(pd.to_numeric, errors="coerce")
                st.success("Plate values updated.")
    
            if SS["assay_plate"] is not None:
                st.divider()
                st.subheader("Plate map")
                st.plotly_chart(
                    _plate_fig(SS["assay_plate"], _build_std_wells_map(), _build_sample_map(),
                               SS["assay_conc_unit"], SS["assay_sig_unit"]),
                    use_container_width=True, config={"displayModeBar": False},
                )
    
        # ── AS2 · Standards ───────────────────────────────────────────────────────
        with AS2:
            if SS["assay_plate"] is None:
                st.info("Import plate data in the **Import** tab first.")
            else:
                st.subheader("Standard concentrations & well positions")
                st.caption(
                    "One row per concentration level. **The first row is the blank** "
                    "(its mean signal is subtracted from all others). "
                    "Enter the well address for each of the 3 replicate sets — "
                    "leave a cell blank if that set doesn't include this level."
                )
                # Both editors + Apply share one st.form so the button's rerun
                # reads their live grid values at submit time, instead of
                # depending on a separate blur event racing the click.
                with st.form(key="assay_std_form"):
                    _a2_std_edit = st.data_editor(
                        SS["assay_std_df"],
                        key="assay_std_editor",
                        num_rows="dynamic",
                        use_container_width=True,
                        column_config={
                            "Label": st.column_config.TextColumn(
                                "Label", help="e.g. 'Blank', '10 µM'"),
                            "Conc": st.column_config.NumberColumn(
                                f"Conc ({SS['assay_conc_unit']})", format="%.5g",
                                help="Known analyte concentration"),
                            "S1": st.column_config.TextColumn("Set 1 well", help="e.g. A1"),
                            "S2": st.column_config.TextColumn("Set 2 well", help="e.g. B1"),
                            "S3": st.column_config.TextColumn("Set 3 well", help="e.g. C1"),
                        },
                    )
    
                    st.divider()
                    st.subheader("Sample well labels  *(optional)*")
                    st.caption(
                        "Every well not listed as a standard is treated as a sample. "
                        "Add rows here to assign group names — used as labels in the results table."
                    )
                    _a2_samp_edit = st.data_editor(
                        SS["assay_sample_df"],
                        key="assay_samp_editor",
                        num_rows="dynamic",
                        use_container_width=True,
                        column_config={
                            "Well":  st.column_config.TextColumn("Well",  help="e.g. D1, E4, H12"),
                            "Label": st.column_config.TextColumn("Label", help="e.g. 'Patient 1'"),
                        },
                    )
    
                    _a2_apply_clicked = st.form_submit_button("Apply layout", type="primary")
    
                if _a2_apply_clicked:
                    SS["assay_std_df"]    = _a2_std_edit.copy()
                    SS["assay_sample_df"] = _a2_samp_edit.copy()
                    SS["assay_std_res"]   = None
                    st.success("Layout saved — head to **Standard Curve** to fit the regression.")
                    st.rerun()
    
                st.divider()
                st.subheader("Layout preview")
                st.plotly_chart(
                    _plate_fig(SS["assay_plate"], _build_std_wells_map(), _build_sample_map(),
                               SS["assay_conc_unit"], SS["assay_sig_unit"]),
                    use_container_width=True, config={"displayModeBar": False},
                )
    
        # ── AS3 · Standard Curve ──────────────────────────────────────────────────
        with AS3:
            if SS["assay_plate"] is None:
                st.info("Import plate data in the **Import** tab first.")
            else:
                _a3c1, _a3c2 = st.columns(2)
                _a3_fit_lbl = _a3c1.selectbox(
                    "Fit type",
                    ["Linear", "Quadratic", "4-Parameter Logistic (4PL)"],
                    key="assay_fit",
                    help=(
                        "**Linear** — straight-line fit. Good for narrow dynamic ranges.\n\n"
                        "**Quadratic** — parabolic fit for slightly curved responses.\n\n"
                        "**4PL** — sigmoidal curve commonly used for ELISA / competitive assays."
                    ),
                )
                _a3_show_reps = _a3c2.checkbox(
                    "Show individual replicates", value=True, key="assay_show_reps",
                )
    
                if st.button("Compute standard curve", type="primary", key="assay_compute"):
                    _a3_sdf  = SS["assay_std_df"].dropna(subset=["Conc"]).reset_index(drop=True)
                    _a3_pl   = SS["assay_plate"]
                    if len(_a3_sdf) < 2:
                        st.error("Need at least 2 concentration levels (including blank).")
                        st.stop()
    
                    # Collect raw signal values: shape (n_levels, 3)
                    _a3_raw = np.array([
                        [_plate_get(_a3_pl, str(_r.get(sc, "")).strip().upper())
                         for sc in ["S1", "S2", "S3"]]
                        for _, _r in _a3_sdf.iterrows()
                    ], dtype=float)
    
                    _a3_blank_pos = int(np.argmin(_a3_sdf["Conc"].values))
                    _a3_blank = float(np.nanmean(_a3_raw[_a3_blank_pos]))
                    if not np.isfinite(_a3_blank):
                        st.error("Blank row has no valid signal. Check well addresses in **Standards**.")
                        st.stop()
    
                    _a3_delta = _a3_raw - _a3_blank
                    _a3_means = np.nanmean(_a3_delta, axis=1)
                    _a3_sds   = np.nanstd(_a3_delta, axis=1, ddof=1)
                    _a3_concs = _a3_sdf["Conc"].values.astype(float)
                    _a3_lbls  = _a3_sdf["Label"].values
                    _ok       = np.isfinite(_a3_concs) & np.isfinite(_a3_means)
    
                    _a3_fit: dict | None = None
                    if _a3_fit_lbl == "Linear":
                        _lr = lin_reg(_a3_concs[_ok], _a3_means[_ok])
                        if _lr:
                            _a3_fit = dict(type="linear", **_lr)
                    elif _a3_fit_lbl == "Quadratic":
                        if _ok.sum() >= 3:
                            try:
                                _coefs = np.polyfit(_a3_concs[_ok], _a3_means[_ok], 2)
                                _yp3   = np.polyval(_coefs, _a3_concs[_ok])
                                _sst3  = float(np.sum((_a3_means[_ok] - _a3_means[_ok].mean()) ** 2))
                                _r2q   = 1 - float(np.sum((_a3_means[_ok] - _yp3)**2)) / _sst3 if _sst3 > 0 else 0.0
                                _a3_fit = dict(type="quad",
                                               a=float(_coefs[0]), b=float(_coefs[1]),
                                               c=float(_coefs[2]), r2=_r2q)
                            except Exception:
                                st.error("Quadratic fit failed.")
                    else:
                        _a3_fit = _fit_4pl(_a3_concs[_ok], _a3_means[_ok])
                        if _a3_fit is None:
                            st.warning("4PL did not converge — falling back to Linear.")
                            _lr = lin_reg(_a3_concs[_ok], _a3_means[_ok])
                            if _lr:
                                _a3_fit = dict(type="linear", **_lr)
    
                    if _a3_fit is None:
                        st.error("Regression failed — not enough valid data points.")
                        st.stop()
    
                    SS["assay_std_res"] = dict(
                        fit=_a3_fit,
                        concs=_a3_concs.tolist(), labels=_a3_lbls.tolist(),
                        means=_a3_means.tolist(), sds=_a3_sds.tolist(),
                        raw_arr=_a3_raw.tolist(), delta_arr=_a3_delta.tolist(),
                        blank_mean=_a3_blank,
                        std_df=_a3_sdf.to_dict(orient="records"),
                    )
                    st.success("Standard curve computed.")
    
                if SS["assay_std_res"] is not None:
                    _r3  = SS["assay_std_res"]
                    _f3  = _r3["fit"]
                    _cx3 = np.array(_r3["concs"], float)
                    _my3 = np.array(_r3["means"], float)
                    _sy3 = np.array(_r3["sds"],   float)
                    _da3 = np.array(_r3["delta_arr"], float)
                    _lb3 = np.array(_r3["labels"])
                    _vm3 = np.isfinite(_my3) & np.isfinite(_cx3)
    
                    _fig_sc = go.Figure()
                    if _a3_show_reps:
                        for _si3, _rc3 in enumerate([PAL[0], PAL[1], PAL[2]]):
                            _ry3 = _da3[:, _si3]
                            _vr3 = np.isfinite(_ry3) & np.isfinite(_cx3)
                            if _vr3.any():
                                _fig_sc.add_trace(go.Scatter(
                                    x=_cx3[_vr3], y=_ry3[_vr3], name=f"Set {_si3 + 1}",
                                    mode="markers",
                                    marker=dict(symbol="circle-open", size=9,
                                                color=_rc3, line=dict(width=1.5)),
                                ))
                    _fig_sc.add_trace(go.Scatter(
                        x=_cx3[_vm3], y=_my3[_vm3], name="Mean ± SD", mode="markers",
                        marker=dict(symbol="circle", size=11, color="#4c96d7",
                                    line=dict(width=1.5, color="white")),
                        error_y=dict(type="data", array=_sy3[_vm3].tolist(),
                                     visible=True, color="#4c96d7", thickness=1.5, width=5),
                        text=_lb3[_vm3], textposition="top center",
                        textfont=dict(size=10),
                    ))
    
                    _xp3 = np.linspace(max(0.0, float(_cx3[_vm3].min())),
                                       float(_cx3[_vm3].max()), 400)
                    if _f3["type"] == "linear":
                        _yp3  = _f3["slope"] * _xp3 + _f3["intercept"]
                        _b3   = _f3["intercept"]
                        _eq3  = (f"y = {_f3['slope']:.3g}x "
                                 f"{'+ ' if _b3 >= 0 else '− '}{abs(_b3):.3g}"
                                 f"   R² = {_f3['r2']:.4f}")
                    elif _f3["type"] == "quad":
                        _yp3  = _f3["a"]*_xp3**2 + _f3["b"]*_xp3 + _f3["c"]
                        _eq3  = (f"y = {_f3['a']:.3g}x² + {_f3['b']:.3g}x + {_f3['c']:.3g}"
                                 f"   R² = {_f3['r2']:.4f}")
                    else:
                        _yp3  = (_f3["d"] + (_f3["a"] - _f3["d"]) /
                                 (1 + (_xp3 / _f3["c"]) ** _f3["b"]))
                        _eq3  = (f"4PL: a={_f3['a']:.3g}  b={_f3['b']:.3g}  "
                                 f"c={_f3['c']:.3g}  d={_f3['d']:.3g}   R²={_f3['r2']:.4f}")
    
                    _fig_sc.add_trace(go.Scatter(
                        x=_xp3, y=_yp3, name="Fit", mode="lines",
                        line=dict(color="#ff9230", dash="dash", width=2.5), showlegend=True,
                    ))
                    _pt3 = _plot_theme()
                    _fig_sc.update_layout(
                        xaxis_title=f"Concentration ({SS['assay_conc_unit']})",
                        yaxis_title=f"ΔSignal ({SS['assay_sig_unit']})",
                        height=500, template=_pt3["template"],
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=True,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                        hovermode="closest",
                        xaxis=dict(showgrid=True, gridcolor=_pt3["grid"],
                                   linecolor=_pt3["axisline"]),
                        yaxis=dict(showgrid=True, gridcolor=_pt3["grid"],
                                   linecolor=_pt3["axisline"],
                                   zeroline=True, zerolinecolor=_pt3["axisline"]),
                        annotations=[dict(
                            text=_eq3, xref="paper", yref="paper", x=0.02, y=0.98,
                            xanchor="left", yanchor="top",
                            font=dict(size=11, color=_pt3["annot_font"]), showarrow=False,
                            bgcolor="rgba(30,30,30,0.75)" if _pt3["template"] == "plotly_dark" else "rgba(255,255,255,0.75)",
                            bordercolor="#555",
                            borderwidth=1, borderpad=6,
                        )],
                    )
                    st.plotly_chart(_fig_sc, use_container_width=True,
                                    config={"scrollZoom": True, "displayModeBar": True,
                                            "modeBarButtonsToRemove": ["select2d","lasso2d"]})
                    st.download_button(
                        "Download interactive HTML",
                        data=_fig_sc.to_html(include_plotlyjs="cdn"),
                        file_name="standard_curve.html", mime="text/html", key="as3_html",
                    )
    
                    # ── Intermediate computation details ──────────────────────
                    with st.expander("Intermediate values", expanded=True):
                        _raw0_3 = np.array(_r3["raw_arr"][0], float)
                        _bl_vals = [f"{v:.5g}" if np.isfinite(v) else "—"
                                    for v in _raw0_3]
                        st.markdown(
                            f"**Blank signal** (row 0 raw): "
                            f"Set 1 = {_bl_vals[0]}, "
                            f"Set 2 = {_bl_vals[1]}, "
                            f"Set 3 = {_bl_vals[2]}  →  "
                            f"**mean = {_r3['blank_mean']:.5g}** {SS['assay_sig_unit']}"
                        )
    
                        _a3_tbl_mid = []
                        for _ki3 in range(len(_cx3)):
                            _raw3  = np.array(_r3["raw_arr"][_ki3],   float)
                            _del3  = np.array(_r3["delta_arr"][_ki3], float)
                            _a3_tbl_mid.append({
                                "Label": _lb3[_ki3],
                                f"Conc ({SS['assay_conc_unit']})":      f"{_cx3[_ki3]:.5g}",
                                f"S1 raw ({SS['assay_sig_unit']})":     fmt(_raw3[0]),
                                f"S2 raw ({SS['assay_sig_unit']})":     fmt(_raw3[1]),
                                f"S3 raw ({SS['assay_sig_unit']})":     fmt(_raw3[2]),
                                f"S1 Δ ({SS['assay_sig_unit']})":       fmt(_del3[0]),
                                f"S2 Δ ({SS['assay_sig_unit']})":       fmt(_del3[1]),
                                f"S3 Δ ({SS['assay_sig_unit']})":       fmt(_del3[2]),
                                f"Mean Δ ({SS['assay_sig_unit']})":     fmt(_my3[_ki3]),
                                f"SD ({SS['assay_sig_unit']})":         fmt(_sy3[_ki3]),
                                "CV (%)": fmt(abs(_sy3[_ki3] / _my3[_ki3]) * 100
                                              if np.isfinite(_my3[_ki3]) and _my3[_ki3] != 0 else np.nan, 2),
                            })
                        st.dataframe(pd.DataFrame(_a3_tbl_mid),
                                     use_container_width=True, hide_index=True)
    
                    # Summary table
                    st.subheader("Standard summary")
                    _a3_tbl = []
                    for _ki3 in range(len(_cx3)):
                        _raw3 = np.array(_r3["raw_arr"][_ki3], float)
                        _a3_tbl.append({
                            "Label": _lb3[_ki3],
                            f"Conc ({SS['assay_conc_unit']})": f"{_cx3[_ki3]:.5g}",
                            f"Set 1 ({SS['assay_sig_unit']})": fmt(_raw3[0]),
                            f"Set 2 ({SS['assay_sig_unit']})": fmt(_raw3[1]),
                            f"Set 3 ({SS['assay_sig_unit']})": fmt(_raw3[2]),
                            f"Mean Δ ({SS['assay_sig_unit']})": fmt(_my3[_ki3]),
                            f"SD ({SS['assay_sig_unit']})": fmt(_sy3[_ki3]),
                            "CV (%)": fmt(abs(_sy3[_ki3] / _my3[_ki3]) * 100
                                          if np.isfinite(_my3[_ki3]) and _my3[_ki3] != 0 else np.nan, 2),
                        })
                    st.dataframe(pd.DataFrame(_a3_tbl), use_container_width=True, hide_index=True)
    
                    # Static export
                    st.divider()
                    st.subheader("Publication-quality export")
                    with st.expander("Export settings", expanded=False):
                        _a3p1, _a3p2, _a3p3, _a3p4 = st.columns(4)
                        _a3_sty = _a3p1.selectbox("Style", ["Origin","Minimal"], key="as3_sty")
                        _a3_fmt = _a3p2.selectbox("Format", ["SVG","PNG","PDF","TIFF"], key="as3_fmt")
                        _a3_dpi = _a3p3.segmented_control("DPI", [150,300,600], default=300,
                                                            required=True,
                                                            key="as3_dpi", disabled=_a3_fmt in ["SVG","PDF"])
                        _a3_sz  = _a3p4.selectbox(
                            "Width",
                            ["Single (3.5\")","1.5-col (5\")","Double (7\")","Full (6.5\")"],
                            key="as3_sz",
                        )
                    _a3_fsm = {"Single (3.5\")": (3.5, 2.625), "1.5-col (5\")": (5.0, 3.75),
                               "Double (7\")": (7.0, 5.0), "Full (6.5\")": (6.5, 4.5)}
                    _a3_pfs   = _a3_fsm[_a3_sz]
                    _a3_pdpi  = int(_a3_dpi) if _a3_fmt not in ["SVG","PDF"] else 300
                    _a3_pstyl = _a3_sty.lower()
    
                    _a3_prev = render_assay_curve(
                        _r3, _a3_show_reps,
                        SS["assay_conc_unit"], SS["assay_sig_unit"],
                        dpi=96, fmt="png", figsize=_a3_pfs, style=_a3_pstyl,
                    )
                    st.caption("Preview")
                    st.image(_a3_prev, use_container_width=True)
                    st.download_button(
                        f"Download ({_a3_fmt})",
                        data=render_assay_curve(
                            _r3, _a3_show_reps,
                            SS["assay_conc_unit"], SS["assay_sig_unit"],
                            dpi=_a3_pdpi, fmt=_a3_fmt.lower(), figsize=_a3_pfs, style=_a3_pstyl,
                        ),
                        file_name=f"standard_curve.{_a3_fmt.lower()}",
                        mime=_MIME[_a3_fmt.lower()],
                        use_container_width=True, key="as3_pub_dl",
                    )
    
        # ── AS4 · Results & Export ────────────────────────────────────────────────
        with AS4:
            if SS["assay_std_res"] is None or SS["assay_plate"] is None:
                st.info("Compute the **Standard Curve** first.")
            else:
                _r4    = SS["assay_std_res"]
                _f4    = _r4["fit"]
                _bk4   = float(_r4["blank_mean"])
                _cx4   = np.array(_r4["concs"], float)
                _c_min = float(_cx4.min())
                _c_max = float(_cx4.max())
    
                # wells occupied by standards
                _std_w4 = {str(_sr.get(sc, "")).strip().upper()
                           for _sr in _r4["std_df"] for sc in ["S1","S2","S3"]
                           if str(_sr.get(sc,"")).strip()}
    
                _slmap4 = _build_sample_map()
    
                def _back_calc(dy: float) -> float:
                    ft = _f4["type"]
                    if not np.isfinite(dy):
                        return np.nan
                    if ft == "linear":
                        s = _f4["slope"]
                        return float((dy - _f4["intercept"]) / s) if s != 0 else np.nan
                    elif ft == "quad":
                        a, b, c = _f4["a"], _f4["b"], _f4["c"] - dy
                        disc = b**2 - 4*a*c
                        if disc < 0 or a == 0:
                            return np.nan
                        r1 = (-b + np.sqrt(disc)) / (2*a)
                        r2 = (-b - np.sqrt(disc)) / (2*a)
                        pos = [r for r in [r1, r2] if r >= -1e-9]
                        if a < 0 and len(pos) == 2:
                            return np.nan
                        return float(min(pos)) if pos else np.nan
                    else:
                        return _4pl_inv(dy, _f4)
    
                _res4_rows = []
                for _row_lbl4 in _PLATE_ROWS:
                    for _ci4 in range(12):
                        _well4 = f"{_row_lbl4}{_ci4 + 1}"
                        if _well4 in _std_w4:
                            continue
                        _sig4 = _plate_get(SS["assay_plate"], _well4)
                        if not np.isfinite(_sig4):
                            continue
                        _dy4   = _sig4 - _bk4
                        _conc4 = _back_calc(_dy4)
                        _flag4 = ""
                        if np.isfinite(_conc4):
                            if _conc4 < _c_min - 1e-9:
                                _flag4 = "< range"
                            elif _conc4 > _c_max + 1e-9:
                                _flag4 = "> range"
                        else:
                            _flag4 = "undefined"
                        _res4_rows.append({
                            "Well":                                  _well4,
                            "Label":                                 _slmap4.get(_well4, ""),
                            f"Signal ({SS['assay_sig_unit']})":      fmt(_sig4),
                            f"ΔSignal ({SS['assay_sig_unit']})":     fmt(_dy4),
                            f"Conc ({SS['assay_conc_unit']})":       fmt(_conc4) if np.isfinite(_conc4) else "—",
                            "Flag":                                  _flag4,
                        })
    
                _res4_df = pd.DataFrame(_res4_rows)
                if _res4_df.empty:
                    st.info("No sample wells found (all wells are assigned as standards).")
                else:
                    st.subheader("Sample results")
                    st.dataframe(_res4_df, use_container_width=True, hide_index=True)
                    _dl4a, _dl4b = st.columns(2)
                    _dl4a.download_button(
                        "Download results CSV",
                        data=_res4_df.to_csv(index=False).encode(),
                        file_name="assay_results.csv", mime="text/csv", key="as4_res_dl",
                    )
                    _dl4b.download_button(
                        "Download standards CSV",
                        data=pd.DataFrame([{
                            "Label": _r4["labels"][i],
                            f"Conc ({SS['assay_conc_unit']})": _r4["concs"][i],
                            f"Set 1 ({SS['assay_sig_unit']})": _r4["raw_arr"][i][0],
                            f"Set 2 ({SS['assay_sig_unit']})": _r4["raw_arr"][i][1],
                            f"Set 3 ({SS['assay_sig_unit']})": _r4["raw_arr"][i][2],
                            f"Mean Δ ({SS['assay_sig_unit']})": _r4["means"][i],
                            f"SD ({SS['assay_sig_unit']})": _r4["sds"][i],
                        } for i in range(len(_r4["concs"]))]).to_csv(index=False).encode(),
                        file_name="standard_curve_data.csv", mime="text/csv", key="as4_std_dl",
                    )
    
                st.divider()
                st.subheader("Results plate map")
                _a4_sw = _build_std_wells_map()
                st.plotly_chart(
                    _plate_fig(SS["assay_plate"], _a4_sw, _slmap4,
                               SS["assay_conc_unit"], SS["assay_sig_unit"]),
                    use_container_width=True, config={"displayModeBar": False},
                )
    
