"""Cyclic Voltammetry mode: import, CV plot, peak analysis, scan-rate analysis, export."""

import io

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.constants import PAL, _MIME, _plot_theme, fmt
from core.numeric import to_num, lin_reg
from core.parsing import parse_potentiostat_csv
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style

SS = st.session_state


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



def render() -> None:
    # NOTE: parses CSVs inline here (via parse_potentiostat_csv) rather than
    # reusing core.shared_tabs._render_import_tab like Amperometry/Solid-State
    # do — pre-existing duplication, not part of the app.py-split's scope.
        import re as _re
    
        CV1, CV2, CV3, CV4, CV5 = st.tabs([
            "① Import", "② CV Plot", "③ Peak Analysis",
            "④ Scan Rate Analysis", "⑤ Export",
        ])
    
        # Deduplicate column names: "Potential (V), Current, Potential (V), …"
        # → "Potential (V) [scan 1], Current [scan 1], Potential (V) [scan 2], …"
        def _dedup_cols(cols: list[str]) -> list[str]:
            from collections import Counter
            cnt = Counter(cols)
            seen: dict[str, int] = {}
            out = []
            for c in cols:
                if cnt[c] > 1:
                    seen[c] = seen.get(c, 0) + 1
                    out.append(f"{c} [scan {seen[c]}]")
                else:
                    out.append(c)
            return out
    
        def _render_cv_plot(figsize, fmt, dpi, rc, style):
            """All-runs CV plot (Viridis by scan rate, dash by channel)."""
            import matplotlib.cm as _mcm
            with matplotlib.rc_context(rc):
                _fg, _ax = plt.subplots(figsize=figsize)
                _n = len(SS.cv_runs)
                _cm = _mcm.get_cmap("viridis", max(1, _n))
                for _ri, _rn in enumerate(SS.cv_runs):
                    _cl = _cm(_ri / max(1, _n - 1))
                    for _ci, _ch in enumerate(_rn["channels"]):
                        _vv = to_num(_rn["df"][_ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                        _ii = to_num(_rn["df"][_ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                        _ax.plot(_vv, _ii, color=_cl,
                                 linestyle=["-", "--", ":", "-."][_ci % 4],
                                 linewidth=1.4,
                                 label=_rn["label"] if _ci == 0 else None)
                        # Detected peaks — matches the interactive chart (CV3)
                        # so the exported figure isn't missing them.
                        _pk = _rn.get("peaks", {}).get(_ch["name"], {})
                        for _p in _pk.get("anodic", []):
                            _ax.plot(_p["Ep"], _p["Ip"], "^", color=_cl,
                                      markersize=8, zorder=5)
                        for _p in _pk.get("cathodic", []):
                            _ax.plot(_p["Ep"], _p["Ip"], "v", color=_cl,
                                      markersize=8, zorder=5)
                _ax.axhline(0, color="#bbbbbb", linewidth=0.8, linestyle="--")
                _ax.set_xlabel(f"Potential ({SS.volt_unit})")
                _ax.set_ylabel(f"Current ({SS.cv_cur_unit})")
                _ax.legend(fontsize=7, loc="upper left",
                           bbox_to_anchor=(1.02, 1), borderaxespad=0)
                _apply_spine_style(_ax, style)
                _bf = io.BytesIO()
                _fg.savefig(_bf, format=fmt, dpi=dpi, bbox_inches="tight")
                plt.close(_fg)
            _bf.seek(0)
            return _bf.getvalue()
    
        def _render_sr_analysis(kind, sel_chs, ch_data, figsize, fmt, dpi, rc, style):
            """Scan rate analysis plot: ip_nu / ip_sqrt_nu / ep_nu / delta_ep."""
            with matplotlib.rc_context(rc):
                _fg, _ax = plt.subplots(figsize=figsize)
                for _ci, _cn in enumerate(sel_chs):
                    _cl = PAL[_ci % len(PAL)]
                    _dd = ch_data[_cn]
                    _nu = _dd["scan_rate"].values
    
                    if kind == "delta_ep":
                        # Single trace per channel — ΔEp has no anodic/cathodic split
                        _yv = _dd["delta_Ep"].values
                        _ok = np.isfinite(_yv)
                        if _ok.any():
                            _ax.plot(_nu[_ok], _yv[_ok], color=_cl, linestyle="-",
                                     marker="o", markersize=6, linewidth=1.4, label=_cn)
                    else:
                        for _pt, _ipc, _epc, _mk, _ls in [
                            ("anodic",   "Ip_a", "Ep_a", "^", "-"),
                            ("cathodic", "Ip_c", "Ep_c", "v", "--"),
                        ]:
                            if kind == "ep_nu":
                                _yv = _dd[_epc].values
                            else:
                                _yv = _dd[_ipc].values
                            _xv = np.sqrt(_nu) if kind == "ip_sqrt_nu" else _nu
                            _ok = np.isfinite(_yv)
                            if not _ok.any():
                                continue
                            _ax.plot(_xv[_ok], _yv[_ok], color=_cl, linestyle=_ls,
                                     marker=_mk, markersize=6, linewidth=1.4,
                                     label=f"{_cn} ({_pt})")
                            if kind == "ip_sqrt_nu":
                                _fit = lin_reg(_xv[_ok], _yv[_ok])
                                if _fit:
                                    _xf = np.linspace(_xv[_ok].min(), _xv[_ok].max(), 200)
                                    _ax.plot(_xf, _fit["slope"] * _xf + _fit["intercept"],
                                             color=_cl, linestyle=":", linewidth=1.2)
                _xlbls = {
                    "ip_nu":      f"Scan rate ν ({SS.cv_sr_unit})",
                    "ip_sqrt_nu": f"√ Scan rate  √ν  (√{SS.cv_sr_unit})",
                    "ep_nu":      f"Scan rate ν ({SS.cv_sr_unit})",
                    "delta_ep":   f"Scan rate ν ({SS.cv_sr_unit})",
                }
                _ylbls = {
                    "ip_nu":      f"Peak current Ip ({SS.cv_cur_unit})",
                    "ip_sqrt_nu": f"Peak current Ip ({SS.cv_cur_unit})",
                    "ep_nu":      f"Potential ({SS.volt_unit})",
                    "delta_ep":   f"ΔEp ({SS.volt_unit})",
                }
                _ax.set_xlabel(_xlbls[kind])
                _ax.set_ylabel(_ylbls[kind])
                _ax.legend(fontsize=7, loc="upper left",
                           bbox_to_anchor=(1.02, 1), borderaxespad=0)
                _apply_spine_style(_ax, style)
                _bf = io.BytesIO()
                _fg.savefig(_bf, format=fmt, dpi=dpi, bbox_inches="tight")
                plt.close(_fg)
            _bf.seek(0)
            return _bf.getvalue()
    
        # ── pub-export settings widget (reused across CV2 and CV4) ────────────────
        def _cv_pub_settings(key_prefix):
            with st.expander("Export settings", expanded=False):
                _c1, _c2, _c3, _c4 = st.columns(4)
                _sty = _c1.selectbox("Style",  ["Origin", "Minimal"],
                                      key=f"{key_prefix}_sty")
                _fmt = _c2.selectbox("Format", ["SVG", "PNG", "PDF", "TIFF"],
                                      key=f"{key_prefix}_fmt")
                _dpi = _c3.segmented_control("DPI", [150, 300, 600], default=300,
                                              key=f"{key_prefix}_dpi",
                                              disabled=_fmt in ["SVG", "PDF"])
                _sz  = _c4.selectbox(
                    "Width",
                    ["Single (3.5\")", "1.5-col (5\")", "Double (7\")", "Full (6.5\")"],
                    key=f"{key_prefix}_sz",
                )
            _fsm = {"Single (3.5\")": (3.5, 2.625), "1.5-col (5\")": (5.0, 3.75),
                    "Double (7\")":   (7.0, 5.0),   "Full (6.5\")":  (6.5, 4.5)}
            return (
                _sty.lower(),
                _fmt.lower(),
                # segmented_control can be clicked off to None (no `required`
                # kwarg in current Streamlit) — fall back to the 300 default.
                int(_dpi) if (_dpi is not None and _fmt not in ["SVG", "PDF"]) else 300,
                _fsm[_sz],
                {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(_sty.lower(), {}),
            )
    
        # ── CV1 · Import ──────────────────────────────────────────────────────────
        with CV1:
            st.subheader("Upload CV Files")
            st.caption(
                "Upload one CSV per scan rate. Column mapping is configured once from the first "
                "file and applied to all — files in a scan rate series share the same structure."
            )
            u1, u2, u3 = st.columns(3)
            SS.volt_unit   = u1.text_input("Potential unit", SS.volt_unit,   help="e.g. V, mV")
            SS.cv_cur_unit = u2.text_input("Current unit",   SS.cv_cur_unit, help="e.g. µA, nA")
            SS.cv_sr_unit  = u3.text_input("Scan rate unit", SS.cv_sr_unit,  help="e.g. mV/s, V/s")
            st.divider()
    
            _up_files = st.file_uploader(
                "Drop CV files here — one per scan rate",
                type=["csv", "txt"], accept_multiple_files=True, key="cv_multi_up",
            )
    
            if _up_files:
                st.markdown("**Assign a scan rate to each file:**")
                st.caption(
                    "Pre-filled from the last number found in each filename — "
                    "double-check it (a date or other number in the filename "
                    "can be picked up by mistake) and correct before loading."
                )
                _sr_vals = {}
                for _f in _up_files:
                    _nums = _re.findall(r"\d+\.?\d*", _f.name.rsplit(".", 1)[0])
                    _dflt = float(_nums[-1]) if _nums else 10.0
                    _fc1, _fc2 = st.columns([4, 1])
                    _fc1.caption(_f.name)
                    _sr_vals[_f.name] = _fc2.number_input(
                        f"ν ({SS.cv_sr_unit})", value=max(_dflt, 0.001), min_value=0.001,
                        step=0.0, format="%g",
                        key=f"cv_sr_{_f.name}", label_visibility="collapsed",
                    )
    
                st.divider()
                st.subheader("Column Mapping")
                _cv_fmt  = st.selectbox(
                    "File format",
                    ["Standard CSV", "Multi-channel instrument (potentiostat, etc.)"],
                    key="cv_imp_fmt",
                    help=(
                        "Choose **Multi-channel instrument** for files with metadata/header rows "
                        "above the numeric data (Bio-Logic, CH Instruments, etc.) — the parser "
                        "finds the data start automatically. Use **Standard CSV** for plain files "
                        "and set **Rows to skip** if there are preamble lines."
                    ),
                )
                _cvimp_c1, _cvimp_c2 = st.columns(2)
                _cv_del_l = _cvimp_c1.selectbox(
                    "Delimiter",
                    ["Auto-detect", "Comma  ,", "Tab  \\t", "Semicolon  ;", "Space"],
                    key="cv_imp_del",
                )
                _cv_skip = int(_cvimp_c2.number_input(
                    "Rows to skip before header", 0, 50, 0,
                    key="cv_imp_skip",
                    help="Only applies to Standard CSV mode. Multi-channel mode finds the data start automatically.",
                ))
                _dmap_cv2 = {"Auto-detect": None, "Comma  ,": ",", "Tab  \\t": "\t",
                             "Semicolon  ;": ";", "Space": r"\s+"}
                _d_cv2 = _dmap_cv2[_cv_del_l]
    
                _all_cols_cv2, _auto_chs_cv2 = [], []
                try:
                    _f0      = _up_files[0]
                    _bytes0  = _f0.read()
                    _f0.seek(0)
                    if _bytes0[:2] in (b"\xff\xfe", b"\xfe\xff"):
                        _raw0 = _bytes0.decode("utf-16")
                    else:
                        _raw0 = _bytes0.decode("utf-8", errors="replace")
    
                    if _d_cv2 is None:
                        _lines0 = _raw0.splitlines()
                        _sniff0 = (_lines0[_cv_skip]
                                   if _cv_skip < len(_lines0)
                                   else (_lines0[0] if _lines0 else ""))
                        _d_cv2 = next((c for c in [",", "\t", ";"] if c in _sniff0), r"\s+")
    
                    if _cv_fmt.startswith("Multi"):
                        _df0, _auto_chs_cv2 = parse_potentiostat_csv(_raw0, _d_cv2, mode="cv")
                        _df0.columns = _dedup_cols(list(_df0.columns))
                        # Drop auto-detected channel refs whose column names were renamed
                        _auto_chs_cv2 = [
                            ch for ch in _auto_chs_cv2
                            if ch.get("vc") in _df0.columns and ch.get("ic") in _df0.columns
                        ]
                    else:
                        _df0 = pd.read_csv(
                            io.StringIO(_raw0), sep=_d_cv2, skiprows=_cv_skip,
                            engine="python" if _d_cv2 == r"\s+" else "c",
                            skipinitialspace=True,
                        )
                        _df0.columns = _dedup_cols([c.lstrip("﻿").strip() for c in _df0.columns])
                    st.dataframe(_df0.head(5), use_container_width=True)
                    _all_cols_cv2 = list(_df0.columns)
                except Exception as _exc_cv2:
                    st.error(f"Could not parse {_up_files[0].name}: {_exc_cv2}")
    
                if _all_cols_cv2:
                    _n_ch_cv2 = int(st.number_input(
                        "Number of channels", 1, 8,
                        value=min(8, len(_auto_chs_cv2) or max(1, len(_all_cols_cv2) // 2)),
                        key="cv_imp_nch",
                    ))
                    _ha2, _hb2, _hc2 = st.columns([2, 3, 3])
                    _ha2.markdown("**Channel name**")
                    _hb2.markdown("**Voltage column**")
                    _hc2.markdown("**Current column(s)** — select multiple to average")
    
                    _ch_map_cv2 = []
                    for _i2 in range(_n_ch_cv2):
                        _pre2    = _auto_chs_cv2[_i2] if _i2 < len(_auto_chs_cv2) else {}
                        _ca2, _cb2, _cc2 = st.columns([2, 3, 3])
    
                        _cname2 = _ca2.text_input(
                            "n", _pre2.get("name", f"CH{_i2+1}"),
                            key=f"cv2_n{_i2}", label_visibility="collapsed",
                        )
    
                        _dvc = _pre2.get("vc", _all_cols_cv2[min(_i2*2, len(_all_cols_cv2)-1)])
                        _cvc2 = _cb2.selectbox(
                            "v", _all_cols_cv2,
                            index=(_all_cols_cv2.index(_dvc) if _dvc in _all_cols_cv2 else 0),
                            key=f"cv2_v{_i2}", label_visibility="collapsed",
                        )
    
                        # Multiselect: one column = direct, multiple = auto-averaged at load time
                        _dic = _pre2.get("ic", _all_cols_cv2[min(_i2*2+1, len(_all_cols_cv2)-1)])
                        _dic_list = [_dic] if _dic in _all_cols_cv2 else []
                        _cic_cols2 = _cc2.multiselect(
                            "i", _all_cols_cv2,
                            default=_dic_list,
                            key=f"cv2_ic{_i2}", label_visibility="collapsed",
                            help=(
                                "One column → used directly. "
                                "Multiple columns → their currents are averaged (e.g. scans 2 and 3)."
                            ),
                        )
                        _ch_map_cv2.append({"name": _cname2, "vc": _cvc2, "ic_cols": _cic_cols2})
    
                    if st.button("Load All Files", type="primary"):
                        _runs_new, _errs_new = [], []
                        # Keyed by (filename, scan_rate) so re-loading the same
                        # file at the same assigned scan rate doesn't silently
                        # wipe previously-computed peak analysis.
                        _existing_peaks = {
                            (_r["filename"], _r["scan_rate"]): _r["peaks"]
                            for _r in SS.cv_runs
                        }
                        for _fup in _up_files:
                            try:
                                _bytes_fup = _fup.read()
                                if _bytes_fup[:2] in (b"\xff\xfe", b"\xfe\xff"):
                                    _raw_fup = _bytes_fup.decode("utf-16")
                                else:
                                    _raw_fup = _bytes_fup.decode("utf-8", errors="replace")
                                if _cv_fmt.startswith("Multi"):
                                    _df_run, _ = parse_potentiostat_csv(_raw_fup, _d_cv2, mode="cv")
                                    _df_run.columns = _dedup_cols(list(_df_run.columns))
                                else:
                                    _df_run = pd.read_csv(
                                        io.StringIO(_raw_fup), sep=_d_cv2, skiprows=_cv_skip,
                                        engine="python" if _d_cv2 == r"\s+" else "c",
                                        skipinitialspace=True,
                                    )
                                    _df_run.columns = _dedup_cols(
                                        [c.lstrip("﻿").strip() for c in _df_run.columns]
                                    )
    
                                # Build channel list — average when multiple ic_cols are given
                                _channels = []
                                for _chd in _ch_map_cv2:
                                    _ics = _chd["ic_cols"]
                                    if not _ics:
                                        continue
                                    if len(_ics) == 1:
                                        _ic_col = _ics[0]
                                    else:
                                        _ic_arrs = [
                                            to_num(_df_run[c]).to_numpy(dtype=float, na_value=np.nan)
                                            for c in _ics if c in _df_run.columns
                                        ]
                                        if not _ic_arrs:
                                            continue
                                        _ml = max(len(a) for a in _ic_arrs)
                                        _mt = np.full((len(_ic_arrs), _ml), np.nan)
                                        for _jj, _aa in enumerate(_ic_arrs):
                                            _mt[_jj, :len(_aa)] = _aa
                                        _ic_col = f"__avg_{_chd['name']}_ic"
                                        _df_run[_ic_col] = np.nanmean(_mt, axis=0)
                                    _channels.append({
                                        "name":   _chd["name"],
                                        "vc":     _chd["vc"],
                                        "ic":     _ic_col,
                                        "is_avg": len(_ics) > 1,
                                    })
    
                                _sr_val = float(_sr_vals[_fup.name])
                                _runs_new.append({
                                    "scan_rate": _sr_val,
                                    "label":     f"{_sr_val:g} {SS.cv_sr_unit}",
                                    "filename":  _fup.name,
                                    "df":        _df_run,
                                    "channels":  _channels,
                                    "peaks":     _existing_peaks.get((_fup.name, _sr_val), {}),
                                })
                            except Exception as _exc_fup:
                                _errs_new.append(f"{_fup.name}: {_exc_fup}")
                        for _e in _errs_new:
                            st.error(_e)
                        _sr_counts = {r["scan_rate"]: 0 for r in _runs_new}
                        for _r in _runs_new:
                            _sr_counts[_r["scan_rate"]] += 1
                        _dupe_srs = sorted(sr for sr, n in _sr_counts.items() if n > 1)
                        if _dupe_srs:
                            st.warning(
                                "Duplicate scan rate(s) assigned: "
                                + ", ".join(f"{sr:g} {SS.cv_sr_unit}" for sr in _dupe_srs)
                                + " — Scan Rate Analysis will plot/fit these as separate "
                                  "points at the same x-value. Assign unique rates above "
                                  "if that's not intended."
                            )
                        _runs_new.sort(key=lambda r: r["scan_rate"])
                        SS.cv_runs = _runs_new
                        st.success(f"Loaded {len(_runs_new)} file(s).")
    
            if SS.cv_runs:
                st.divider()
                st.subheader("Loaded Runs")
                st.dataframe(pd.DataFrame([{
                    f"Scan rate ({SS.cv_sr_unit})": r["scan_rate"],
                    "File":     r["filename"],
                    "Rows":     len(r["df"]),
                    "Channels": ", ".join(
                        c["name"] + (" ⌀" if c.get("is_avg") else "")
                        for c in r["channels"]
                    ),
                    "Peaks":    "✓" if r["peaks"] else "—",
                } for r in SS.cv_runs]), use_container_width=True, hide_index=True)
    
    
        # ── CV2 · CV Plot ──────────────────────────────────────────────────────────
        with CV2:
            if not SS.cv_runs:
                st.info("Import CV files in the **Import** tab first.")
            else:
                _all_chs_p = list(dict.fromkeys(c["name"] for r in SS.cv_runs for c in r["channels"]))
                _all_srs_p = [r["label"] for r in SS.cv_runs]
    
                # Initialise / repair multiselect keys when runs change
                if "cv2p_srs" not in SS or any(s not in _all_srs_p for s in SS.get("cv2p_srs", [])):
                    SS["cv2p_srs"] = _all_srs_p[:]
                if "cv2p_chs" not in SS or any(c not in _all_chs_p for c in SS.get("cv2p_chs", [])):
                    SS["cv2p_chs"] = _all_chs_p[:]
    
                # Scan rate solo buttons
                if len(_all_srs_p) >= 2:
                    _siso_cols = st.columns([1.4] + [1] * len(_all_srs_p))
                    _siso_cols[0].markdown("**Isolate ν:**",
                                           help="Click to show only that scan rate")
                    for _ji_s, _sri in enumerate(_all_srs_p):
                        if _siso_cols[_ji_s + 1].button(
                            _sri, key=f"cv2p_sr_solo_{_ji_s}",
                            use_container_width=True, help=f"Show only {_sri}",
                        ):
                            SS["cv2p_srs"] = [_sri]
    
                _vis_srs_p = st.multiselect("Scan rates", _all_srs_p, key="cv2p_srs")
    
                # Channel solo buttons
                if len(_all_chs_p) >= 2:
                    _ciso_cols = st.columns([1.4] + [1] * len(_all_chs_p))
                    _ciso_cols[0].markdown("**Isolate channel:**",
                                           help="Click to show only that channel")
                    for _ji_c, _chi_p in enumerate(_all_chs_p):
                        if _ciso_cols[_ji_c + 1].button(
                            _chi_p, key=f"cv2p_ch_solo_{_ji_c}",
                            use_container_width=True, help=f"Show only {_chi_p}",
                        ):
                            SS["cv2p_chs"] = [_chi_p]
    
                _vis_chs_p = st.multiselect("Channels", _all_chs_p, key="cv2p_chs")
                # Colour by channel (PAL); opacity encodes scan rate (dim=slow, bright=fast)
                _vis_runs_p = [r for r in SS.cv_runs if r["label"] in _vis_srs_p]
                _n_vis_p    = len(_vis_runs_p)
                _fig_cvp    = go.Figure()
    
                for _rank_p, _run_p in enumerate(_vis_runs_p):
                    _opacity_p = 0.30 + 0.70 * (_rank_p / max(1, _n_vis_p - 1))
                    for _ci_p, _ch_p in enumerate(_run_p["channels"]):
                        if _ch_p["name"] not in _vis_chs_p:
                            continue
                        _col_p = PAL[_ci_p % len(PAL)]
                        _vp = to_num(_run_p["df"][_ch_p["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                        _ip = to_num(_run_p["df"][_ch_p["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                        _fig_cvp.add_trace(go.Scatter(
                            x=_vp, y=_ip,
                            name=_run_p["label"],
                            legendgroup=_ch_p["name"],
                            legendgrouptitle=dict(text=_ch_p["name"]),
                            mode="lines",
                            opacity=_opacity_p,
                            line=dict(color=_col_p, width=1.8),
                        ))
                        for _ptkey_p, _sym_p in [("anodic","triangle-up"),("cathodic","triangle-down")]:
                            for _pp in _run_p["peaks"].get(_ch_p["name"], {}).get(_ptkey_p, []):
                                _fig_cvp.add_trace(go.Scatter(
                                    x=[_pp["Ep"]], y=[_pp["Ip"]],
                                    mode="markers", showlegend=False,
                                    legendgroup=_ch_p["name"],
                                    opacity=_opacity_p,
                                    marker=dict(symbol=_sym_p, size=10, color=_col_p,
                                                line=dict(width=1, color="white")),
                                ))
    
                _pt = _plot_theme()
                _fig_cvp.add_hline(y=0, line=dict(color=_pt["axisline"], width=1, dash="dash"))
                _fig_cvp.update_layout(
                    xaxis_title=f"Potential ({SS.volt_unit})",
                    yaxis_title=f"Current ({SS.cv_cur_unit})",
                    height=560, template=_pt["template"],
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    showlegend=True, hovermode="closest",
                    legend=dict(
                        orientation="v", x=1.02, y=1, xanchor="left",
                        groupclick="toggleitem",
                    ),
                    xaxis=dict(showgrid=True, gridcolor=_pt["grid"],
                               linecolor=_pt["axisline"]),
                    yaxis=dict(showgrid=True, gridcolor=_pt["grid"],
                               linecolor=_pt["axisline"]),
                )
                st.plotly_chart(_fig_cvp, use_container_width=True, key="cv2_plot_chart",
                                config={"scrollZoom": True, "displayModeBar": True,
                                        "modeBarButtonsToRemove": ["select2d","lasso2d"]})
                st.caption(
                    "Colour → channel (grouped in legend). "
                    f"Opacity → scan rate (dim = slowest {SS.cv_sr_unit}, bright = fastest)."
                )
                st.download_button("Download interactive HTML",
                                   data=_fig_cvp.to_html(include_plotlyjs="cdn"),
                                   file_name="cv_plot.html", mime="text/html", key="cv2p_html")
    
                st.divider()
                st.markdown("#### Publication-quality export")
                _sty2p, _fmt2p, _dpi2p, _fs2p, _rc2p = _cv_pub_settings("cv2p_pub")
                _prev2p = _render_cv_plot(_fs2p, "png", 96, _rc2p, _sty2p)
                st.caption("Preview")
                st.image(_prev2p, use_container_width=True)
                st.download_button(
                    f"Download CV plot ({_fmt2p.upper()})",
                    data=_render_cv_plot(_fs2p, _fmt2p, _dpi2p, _rc2p, _sty2p),
                    file_name=f"cv_plot_pub.{_fmt2p}", mime=_MIME[_fmt2p],
                    use_container_width=True, key="cv2p_pub_dl",
                )
    
        # ── CV3 · Peak Analysis ───────────────────────────────────────────────────
        with CV3:
            if not SS.cv_runs:
                st.info("Import CV files in the **Import** tab first.")
            else:
                _all_chs3 = list(dict.fromkeys(c["name"] for r in SS.cv_runs for c in r["channels"]))
                _i3_all = []
                for _r3 in SS.cv_runs:
                    for _ch3 in _r3["channels"]:
                        _a3 = to_num(_r3["df"][_ch3["ic"]]).dropna().to_numpy(float)
                        if len(_a3):
                            _i3_all.extend(_a3.tolist())
                _auto_prom3 = float(np.ptp(_i3_all)) * 0.05 if _i3_all else 0.1
                st.subheader("Peak Detection")
                st.caption("Runs `scipy.signal.find_peaks` on every loaded scan rate. "
                           "Results appear as markers on the **CV Plot** tab.")
    
                _p3a, _p3b, _p3c, _p3d = st.columns(4)
                _prom3 = _p3a.number_input(
                    f"Prominence ({SS.cv_cur_unit})", min_value=0.0,
                    value=round(_auto_prom3, 4), format="%.4g", key="cv3_prom",
                    help=(
                        f"Minimum peak height relative to its surrounding baseline. "
                        f"0 = no minimum prominence filter. "
                        f"Auto = 5 % of current range ({_auto_prom3:.3g} {SS.cv_cur_unit})."
                    ),
                )
                _mdist3 = _p3b.number_input(
                    "Min distance (points)", min_value=1, value=10, key="cv3_dist",
                    help="Minimum number of data points between two detected peaks.",
                )
                _width3 = _p3c.number_input(
                    "Min width (points)", min_value=0, value=0, key="cv3_width",
                    help=(
                        "Minimum peak width in data points. "
                        "Use to reject sharp noise spikes; 0 = no minimum width."
                    ),
                )
                _height3 = _p3d.number_input(
                    f"Min |Ip| ({SS.cv_cur_unit})", min_value=0.0, value=0.0,
                    format="%.4g", key="cv3_height",
                    help=(
                        "Minimum absolute peak current for both anodic and cathodic peaks. "
                        "0 = no minimum height filter."
                    ),
                )
                _width3_val  = int(_width3)   if _width3  > 0   else None
                _height3_val = float(_height3) if _height3 > 0.0 else None
    
                _ana_chs3 = st.multiselect("Channels", _all_chs3, default=_all_chs3, key="cv3_chs")
    
                if st.button("Find Peaks in All Runs", type="primary"):
                    for _r3 in SS.cv_runs:
                        _r3["peaks"] = {}
                        for _ch3 in _r3["channels"]:
                            if _ch3["name"] not in _ana_chs3:
                                continue
                            _v3 = to_num(_r3["df"][_ch3["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                            _i3 = to_num(_r3["df"][_ch3["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                            _r3["peaks"][_ch3["name"]] = find_cv_peaks(
                                _v3, _i3, float(_prom3), int(_mdist3),
                                _width3_val, _height3_val,
                            )
                    st.success(f"Peaks found in {len(SS.cv_runs)} run(s). Head to **Scan Rate Analysis**.")
                    st.rerun()
    
                if any(r["peaks"] for r in SS.cv_runs):
                    # ── Per-run peak count summary ─────────────────────────────
                    _psumm3 = []
                    for _r3 in SS.cv_runs:
                        if not _r3["peaks"]:
                            continue
                        for _ch3n, _pk3 in _r3["peaks"].items():
                            _na3 = _pk3.get("anodic", [])
                            _nc3 = _pk3.get("cathodic", [])
                            _pa3 = max(_na3, key=lambda p: abs(p["Ip"]), default=None) if _na3 else None
                            _pc3 = max(_nc3, key=lambda p: abs(p["Ip"]), default=None) if _nc3 else None
                            _psumm3.append({
                                f"Scan rate ({SS.cv_sr_unit})": _r3["scan_rate"],
                                "Channel":                       _ch3n,
                                # ⚠ flags runs with >1 candidate peak, where the
                                # "largest |Ip|" heuristic below can silently pick
                                # a noise spike instead of the intended peak —
                                # worth a visual double-check on the CV Plot tab.
                                "Anodic peaks":                  (f"⚠ {len(_na3)}" if len(_na3) > 1 else len(_na3)),
                                f"Main Ep,a ({SS.volt_unit})":   fmt(_pa3["Ep"]) if _pa3 else "—",
                                f"Main Ip,a ({SS.cv_cur_unit})": fmt(_pa3["Ip"]) if _pa3 else "—",
                                "Cathodic peaks":                (f"⚠ {len(_nc3)}" if len(_nc3) > 1 else len(_nc3)),
                                f"Main Ep,c ({SS.volt_unit})":   fmt(_pc3["Ep"]) if _pc3 else "—",
                                f"Main Ip,c ({SS.cv_cur_unit})": fmt(_pc3["Ip"]) if _pc3 else "—",
                            })
                    if _psumm3:
                        with st.expander("Peak count summary", expanded=True):
                            st.caption(
                                "⚠ marks a run with more than one candidate peak, "
                                "where \"Main Ep/Ip\" (the largest |Ip|) can pick a "
                                "noise spike instead of the intended peak — verify "
                                "visually on the **CV Plot** tab or raise Prominence/"
                                "Min distance above."
                            )
                            st.dataframe(pd.DataFrame(_psumm3), use_container_width=True, hide_index=True)
    
                    # ── Full peak list ─────────────────────────────────────────
                    _ptable3 = []
                    for _r3 in SS.cv_runs:
                        for _ch3n, _pk3 in _r3["peaks"].items():
                            for _p3 in _pk3.get("anodic", []):
                                _ptable3.append({
                                    f"Scan rate ({SS.cv_sr_unit})": _r3["scan_rate"],
                                    "Channel": _ch3n, "Type": "Anodic",
                                    f"Ep ({SS.volt_unit})": fmt(_p3["Ep"]),
                                    f"Ip ({SS.cv_cur_unit})": fmt(_p3["Ip"]),
                                })
                            for _p3 in _pk3.get("cathodic", []):
                                _ptable3.append({
                                    f"Scan rate ({SS.cv_sr_unit})": _r3["scan_rate"],
                                    "Channel": _ch3n, "Type": "Cathodic",
                                    f"Ep ({SS.volt_unit})": fmt(_p3["Ep"]),
                                    f"Ip ({SS.cv_cur_unit})": fmt(_p3["Ip"]),
                                })
                    if _ptable3:
                        with st.expander("All detected peaks", expanded=False):
                            st.dataframe(pd.DataFrame(_ptable3), use_container_width=True, hide_index=True)
    
        # ── CV4 · Scan Rate Analysis ──────────────────────────────────────────────
        with CV4:
            if not SS.cv_runs or not any(r["peaks"] for r in SS.cv_runs):
                st.info("Run **Peak Analysis** first.")
            else:
                _all_chs4 = [
                    ch for ch in
                    list(dict.fromkeys(c["name"] for r in SS.cv_runs for c in r["channels"]))
                    if any(r["peaks"].get(ch) for r in SS.cv_runs)
                ]
                if not _all_chs4:
                    st.info("No peaks detected yet.")
                else:
                    # Initialise / repair key when available channels change
                    if "cv4_chs" not in SS or any(c not in _all_chs4 for c in SS.get("cv4_chs", [])):
                        SS["cv4_chs"] = _all_chs4[:]
    
                    if len(_all_chs4) >= 2:
                        _iso4_cols = st.columns([1.4] + [1] * len(_all_chs4))
                        _iso4_cols[0].markdown("**Isolate channel:**",
                                               help="Click to show only that channel")
                        for _ji4, _chi4_n in enumerate(_all_chs4):
                            if _iso4_cols[_ji4 + 1].button(
                                _chi4_n, key=f"cv4_solo_{_ji4}",
                                use_container_width=True, help=f"Show only {_chi4_n}",
                            ):
                                SS["cv4_chs"] = [_chi4_n]
    
                    _sel_chs4 = st.multiselect("Channels", _all_chs4, key="cv4_chs")
    
                    def _main_peak4(lst):
                        return max(lst, key=lambda p: abs(p["Ip"])) if lst else None
    
                    _ch4_data = {}
                    for _chn4 in _sel_chs4:
                        _rows4 = []
                        for _r4 in SS.cv_runs:
                            _pk4 = _r4["peaks"].get(_chn4, {})
                            _pa4 = _main_peak4(_pk4.get("anodic", []))
                            _pc4 = _main_peak4(_pk4.get("cathodic", []))
                            _Epa4 = _pa4["Ep"] if _pa4 else np.nan
                            _Epc4 = _pc4["Ep"] if _pc4 else np.nan
                            _rows4.append({
                                "scan_rate": _r4["scan_rate"], "label": _r4["label"],
                                "Ip_a":     _pa4["Ip"] if _pa4 else np.nan,
                                "Ep_a":     _Epa4,
                                "Ip_c":     _pc4["Ip"] if _pc4 else np.nan,
                                "Ep_c":     _Epc4,
                                "delta_Ep": (abs(_Epa4 - _Epc4)
                                             if np.isfinite(_Epa4) and np.isfinite(_Epc4)
                                             else np.nan),
                                "E_half":   ((_Epa4 + _Epc4) / 2
                                             if np.isfinite(_Epa4) and np.isfinite(_Epc4)
                                             else np.nan),
                            })
                        _ch4_data[_chn4] = (pd.DataFrame(_rows4)
                                             .sort_values("scan_rate").reset_index(drop=True))
    
                    _pt4 = _plot_theme()
                    _dl4 = dict(
                        template=_pt4["template"],
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=True, height=420,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                        xaxis=dict(showgrid=True, gridcolor=_pt4["grid"],
                                   linecolor=_pt4["axisline"]),
                        yaxis=dict(showgrid=True, gridcolor=_pt4["grid"],
                                   linecolor=_pt4["axisline"]),
                    )
    
                    st.subheader("Peak Current vs Scan Rate")
                    _fig_ip_nu   = go.Figure()
                    _fig_ip_sqrt = go.Figure()
                    _sr_stats4   = []
    
                    for _ci4, _chn4 in enumerate(_sel_chs4):
                        _col4 = PAL[_ci4 % len(PAL)]
                        _d4   = _ch4_data[_chn4]
                        _nu4  = _d4["scan_rate"].values
                        _snu4 = np.sqrt(_nu4)
                        for _pt4, _ipcol4, _sym4, _dash4 in [
                            ("anodic",   "Ip_a", "triangle-up",   "solid"),
                            ("cathodic", "Ip_c", "triangle-down", "dash"),
                        ]:
                            _ip4    = _d4[_ipcol4].values
                            _valid4 = np.isfinite(_ip4)
                            if not _valid4.any():
                                continue
                            _lbl4 = f"{_chn4} ({_pt4})"
                            _fig_ip_nu.add_trace(go.Scatter(
                                x=_nu4[_valid4], y=_ip4[_valid4], name=_lbl4,
                                mode="markers+lines",
                                marker=dict(symbol=_sym4, size=10, color=_col4,
                                            line=dict(width=1, color="white")),
                                line=dict(color=_col4, dash=_dash4),
                            ))
                            _fig_ip_sqrt.add_trace(go.Scatter(
                                x=_snu4[_valid4], y=_ip4[_valid4], name=_lbl4,
                                mode="markers",
                                marker=dict(symbol=_sym4, size=10, color=_col4,
                                            line=dict(width=1, color="white")),
                            ))
                            _fit4 = lin_reg(_snu4[_valid4], _ip4[_valid4])
                            if _fit4:
                                _xf4 = np.linspace(_snu4[_valid4].min(), _snu4[_valid4].max(), 200)
                                _fig_ip_sqrt.add_trace(go.Scatter(
                                    x=_xf4, y=_fit4["slope"]*_xf4 + _fit4["intercept"],
                                    name=f"{_lbl4} fit (R²={_fit4['r2']:.3f})",
                                    mode="lines", showlegend=True,
                                    line=dict(color=_col4, dash="dot", width=2),
                                ))
                                _sr_stats4.append({
                                    "Channel": _chn4, "Peak": _pt4,
                                    f"Slope Ip/√ν ({SS.cv_cur_unit}/√{SS.cv_sr_unit})":
                                        fmt(_fit4["slope"]),
                                    "Intercept": fmt(_fit4["intercept"]),
                                    "R² (Ip vs √ν)": f"{_fit4['r2']:.4f}",
                                    "N runs": int(_valid4.sum()),
                                })
    
                    _fig_ip_nu.update_layout(**_dl4,
                        xaxis_title=f"Scan rate ν ({SS.cv_sr_unit})",
                        yaxis_title=f"Peak current Ip ({SS.cv_cur_unit})")
                    st.plotly_chart(_fig_ip_nu, use_container_width=True, key="cv4_ip_nu")
                    st.download_button(
                        "Download Ip vs ν — HTML",
                        data=_fig_ip_nu.to_html(include_plotlyjs="cdn"),
                        file_name="ip_vs_nu.html", mime="text/html", key="cv4_ip_nu_html",
                    )
    
                    st.subheader("Randles–Ševčík Plot  (Ip vs √ν)")
                    st.caption("Linear Ip vs √ν → **diffusion-controlled**. "
                               "Linear Ip vs ν (above) → **surface-confined** (adsorption-controlled).")
                    _fig_ip_sqrt.update_layout(**_dl4,
                        xaxis_title=f"√ Scan rate  √ν  (√{SS.cv_sr_unit})",
                        yaxis_title=f"Peak current Ip ({SS.cv_cur_unit})")
                    st.plotly_chart(_fig_ip_sqrt, use_container_width=True, key="cv4_ip_sqrt")
                    st.download_button(
                        "Download Ip vs √ν — HTML",
                        data=_fig_ip_sqrt.to_html(include_plotlyjs="cdn"),
                        file_name="ip_vs_sqrt_nu.html", mime="text/html", key="cv4_ip_sqrt_html",
                    )
    
                    if _sr_stats4:
                        st.dataframe(pd.DataFrame(_sr_stats4), use_container_width=True, hide_index=True)
    
                    st.subheader("Peak Potential vs Scan Rate")
                    st.caption("For a fully reversible couple Ep is scan-rate-independent. "
                               "A shift in Ep with ν indicates quasi-reversible or irreversible kinetics.")
                    _fig_ep_nu = go.Figure()
                    for _ci4, _chn4 in enumerate(_sel_chs4):
                        _col4 = PAL[_ci4 % len(PAL)]
                        _d4   = _ch4_data[_chn4]
                        _nu4  = _d4["scan_rate"].values
                        for _pt4e, _epcol4, _sym4e, _dash4e in [
                            ("anodic",   "Ep_a",   "triangle-up",   "solid"),
                            ("cathodic", "Ep_c",   "triangle-down", "dash"),
                            ("E½",       "E_half", "circle",        "dot"),
                        ]:
                            _ep4 = _d4[_epcol4].values
                            _v4e = np.isfinite(_ep4)
                            if not _v4e.any():
                                continue
                            _fig_ep_nu.add_trace(go.Scatter(
                                x=_nu4[_v4e], y=_ep4[_v4e],
                                name=f"{_chn4} {_pt4e}", mode="markers+lines",
                                marker=dict(symbol=_sym4e, size=9, color=_col4,
                                            line=dict(width=1, color="white")),
                                line=dict(color=_col4, dash=_dash4e),
                            ))
                    _fig_ep_nu.update_layout(**_dl4,
                        xaxis_title=f"Scan rate ν ({SS.cv_sr_unit})",
                        yaxis_title=f"Potential ({SS.volt_unit})")
                    st.plotly_chart(_fig_ep_nu, use_container_width=True, key="cv4_ep_nu")
                    st.download_button(
                        "Download Ep vs ν — HTML",
                        data=_fig_ep_nu.to_html(include_plotlyjs="cdn"),
                        file_name="ep_vs_nu.html", mime="text/html", key="cv4_ep_nu_html",
                    )
    
                    st.subheader("Peak Separation (ΔEp) vs Scan Rate")
                    st.caption(
                        "ΔEp = |Ep,a − Ep,c|. "
                        "For a fully reversible couple at 25 °C, ΔEp ≈ 59/n mV. "
                        "ΔEp increasing with scan rate indicates quasi-reversible or "
                        "irreversible electron transfer kinetics."
                    )
                    _fig_dep_nu = go.Figure()
                    _any_dep4 = False
                    for _ci4, _chn4 in enumerate(_sel_chs4):
                        _col4 = PAL[_ci4 % len(PAL)]
                        _d4   = _ch4_data[_chn4]
                        _nu4  = _d4["scan_rate"].values
                        _dep4 = _d4["delta_Ep"].values
                        _vdep = np.isfinite(_dep4)
                        if not _vdep.any():
                            continue
                        _any_dep4 = True
                        _fig_dep_nu.add_trace(go.Scatter(
                            x=_nu4[_vdep], y=_dep4[_vdep],
                            name=_chn4, mode="markers+lines",
                            marker=dict(symbol="circle", size=10, color=_col4,
                                        line=dict(width=1, color="white")),
                            line=dict(color=_col4),
                        ))
                    if not _any_dep4:
                        st.info(
                            "No ΔEp/E½ to plot — every selected channel is missing "
                            "either an anodic or a cathodic peak at every scan rate "
                            "(expected for an irreversible couple, which has only "
                            "one). Check the **Peak Analysis** peak counts if this "
                            "wasn't expected."
                        )
                    else:
                        _fig_dep_nu.update_layout(**_dl4,
                            xaxis_title=f"Scan rate ν ({SS.cv_sr_unit})",
                            yaxis_title=f"ΔEp ({SS.volt_unit})")
                        st.plotly_chart(_fig_dep_nu, use_container_width=True, key="cv4_dep_nu")
                        st.download_button(
                            "Download ΔEp vs ν — HTML",
                            data=_fig_dep_nu.to_html(include_plotlyjs="cdn"),
                            file_name="dep_vs_nu.html", mime="text/html", key="cv4_dep_nu_html",
                        )
    
                    # ── Export ────────────────────────────────────────────────────
                    st.divider()
                    st.markdown("#### Export")
    
                    # CSV
                    _sr_exp4 = []
                    for _chn4 in _sel_chs4:
                        for _, _rw4 in _ch4_data[_chn4].iterrows():
                            _sr_exp4.append({
                                "Channel": _chn4,
                                f"Scan rate ({SS.cv_sr_unit})": _rw4["scan_rate"],
                                f"Ip,a ({SS.cv_cur_unit})":     _rw4["Ip_a"],
                                f"Ep,a ({SS.volt_unit})":        _rw4["Ep_a"],
                                f"Ip,c ({SS.cv_cur_unit})":     _rw4["Ip_c"],
                                f"Ep,c ({SS.volt_unit})":        _rw4["Ep_c"],
                                f"ΔEp ({SS.volt_unit})":         _rw4["delta_Ep"],
                                f"E½ ({SS.volt_unit})":          _rw4["E_half"],
                            })
                    if _sr_exp4:
                        st.download_button(
                            "Download data CSV",
                            data=pd.DataFrame(_sr_exp4).to_csv(index=False).encode(),
                            file_name="cv_scan_rate_analysis.csv", mime="text/csv", key="cv4_dl",
                        )
    
                    # Publication-quality static export
                    st.markdown("**Publication-quality plots**")
                    _e4sty, _e4fmtl, _e4dpiv, _e4fs, _e4rc = _cv_pub_settings("cv4_pub")
    
                    _e4a, _e4b, _e4c, _e4d = st.columns(4)
                    for _e4col, _e4kind, _e4title, _e4fname in [
                        (_e4a, "ip_nu",      "Ip vs ν",    "ip_vs_nu"),
                        (_e4b, "ip_sqrt_nu", "Ip vs √ν",   "ip_vs_sqrtnu"),
                        (_e4c, "ep_nu",      "Ep vs ν",    "ep_vs_nu"),
                        (_e4d, "delta_ep",   "ΔEp vs ν",   "dep_vs_nu"),
                    ]:
                        _prev4 = _render_sr_analysis(
                            _e4kind, _sel_chs4, _ch4_data,
                            _e4fs, "png", 96, _e4rc, _e4sty,
                        )
                        _e4col.caption(f"{_e4title} preview")
                        _e4col.image(_prev4, use_container_width=True)
                        _e4col.download_button(
                            f"{_e4title}  ({_e4fmtl.upper()})",
                            data=_render_sr_analysis(
                                _e4kind, _sel_chs4, _ch4_data,
                                _e4fs, _e4fmtl, _e4dpiv, _e4rc, _e4sty,
                            ),
                            file_name=f"{_e4fname}.{_e4fmtl}",
                            mime=_MIME[_e4fmtl],
                            use_container_width=True,
                            key=f"cv4_pub_{_e4kind}",
                        )
    
        # ── CV5 · Export ──────────────────────────────────────────────────────────
        with CV5:
            if not SS.cv_runs:
                st.info("No CV data loaded.")
            else:
                if any(r["peaks"] for r in SS.cv_runs):
                    st.markdown("#### All peaks")
                    _all_pk5 = []
                    for _r5 in SS.cv_runs:
                        for _ch5n, _pk5 in _r5["peaks"].items():
                            for _p5 in _pk5.get("anodic", []):
                                _all_pk5.append({
                                    f"Scan rate ({SS.cv_sr_unit})": _r5["scan_rate"],
                                    "Channel": _ch5n, "Type": "Anodic",
                                    f"Ep ({SS.volt_unit})":   _p5["Ep"],
                                    f"Ip ({SS.cv_cur_unit})": _p5["Ip"],
                                })
                            for _p5 in _pk5.get("cathodic", []):
                                _all_pk5.append({
                                    f"Scan rate ({SS.cv_sr_unit})": _r5["scan_rate"],
                                    "Channel": _ch5n, "Type": "Cathodic",
                                    f"Ep ({SS.volt_unit})":   _p5["Ep"],
                                    f"Ip ({SS.cv_cur_unit})": _p5["Ip"],
                                })
                    if _all_pk5:
                        st.dataframe(pd.DataFrame(_all_pk5), use_container_width=True, hide_index=True)
                        st.download_button(
                            "Download peaks CSV",
                            data=pd.DataFrame(_all_pk5).to_csv(index=False).encode(),
                            file_name="cv_peaks_all.csv", mime="text/csv", key="cv5_pk_dl",
                        )
    
                st.markdown("#### Raw data by scan rate")
                for _ri5, _r5 in enumerate(SS.cv_runs):
                    _safe5 = _r5["label"].replace("/","per").replace(" ","_")
                    st.download_button(
                        f"Raw — {_r5['label']}",
                        data=_r5["df"].to_csv(index=False).encode(),
                        file_name=f"cv_raw_{_safe5}.csv", mime="text/csv",
                        key=f"cv5_raw_{_ri5}_{_safe5}",
                    )
    
                st.divider()
                st.markdown("#### Publication-quality export")
                _sty5, _fmt5l, _dpi5v, _fs5, _rc5 = _cv_pub_settings("cv5_pub")
                st.caption("Preview")
                st.image(_render_cv_plot(_fs5, "png", 96, _rc5, _sty5), use_container_width=True)
                st.download_button(
                    f"Download CV plot ({_fmt5l.upper()})",
                    data=_render_cv_plot(_fs5, _fmt5l, _dpi5v, _rc5, _sty5),
                    file_name=f"cv_pub.{_fmt5l}", mime=_MIME[_fmt5l],
                    use_container_width=True, key="cv5_pub_dl",
                )
    
