"""
Sensor Calibration Studio  ·  Streamlit app
Import multi-channel amperometric data, define calibration windows,
fit and export calibration curves with sensor statistics.
"""

import io
import json as _json
import time
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import matplotlib
import matplotlib.pyplot as plt
from scipy import stats
from streamlit_local_storage import LocalStorage

matplotlib.use("Agg")   # headless backend — no display required

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sensor Analysis Studio", layout="wide")

# Larger tap targets for touchscreen use — purely cosmetic, no behavior change.
st.markdown("""
<style>
div[data-testid="stButton"] button,
div[data-testid="stDownloadButton"] button,
div[data-testid="stFormSubmitButton"] button {
    min-height: 44px;
    padding-top: 0.5rem;
    padding-bottom: 0.5rem;
}
div[data-testid="stCheckbox"] label, div[data-testid="stRadio"] label {
    min-height: 28px;
    padding: 0.15rem 0;
}
div[data-baseweb="select"] { min-height: 44px; }
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    padding: 0.3rem 0.5rem;
    margin: 0.15rem;
}
</style>
""", unsafe_allow_html=True)

# Config persists in the user's browser (localStorage), not on disk — the
# deployment filesystem is ephemeral and wipes any saved file on redeploy.
_local_storage = LocalStorage()

# ── shared colour palette (up to 8 channels + 1 average) ─────────────────────
PAL = [
    "#4c96d7", "#ff9230", "#2ecc71", "#e05c5c",
    "#b39ddb", "#f0a050", "#f48fb1", "#6d8ea0",
]
AVG_COLOR = "#555555"   # dark charcoal for channel-average — readable on both white and dark backgrounds


def _plot_theme() -> dict:
    """Plotly styling that adapts to the user's actual Streamlit theme (light/dark)."""
    is_dark = st.context.theme.type != "light"   # None (unknown) treated as dark, today's default
    return dict(
        template   = "plotly_dark" if is_dark else "plotly_white",
        grid       = "rgba(255,255,255,0.1)" if is_dark else "rgba(0,0,0,0.12)",
        axisline   = "rgba(255,255,255,0.2)" if is_dark else "rgba(0,0,0,0.25)",
        spike      = "#888" if is_dark else "#555",
        annot_font = "#e0e0e0" if is_dark else "#222",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def smooth_signal(arr: np.ndarray, method: str, window: int, polyorder: int = 2) -> np.ndarray:
    """Optional smoothing for a 1-D signal. Returns arr unchanged if method == 'None'."""
    if method == "None" or arr.size == 0:
        return arr
    window = max(3, int(window) | 1)   # coerce to odd, >= 3
    if method == "Moving average":
        return pd.Series(arr).rolling(window, center=True, min_periods=1).mean().to_numpy()
    if method == "Savitzky-Golay":
        from scipy.signal import savgol_filter
        window = min(window, arr.size if arr.size % 2 else arr.size - 1)
        if window < 3:
            return arr
        po = min(int(polyorder), window - 1)
        return savgol_filter(arr, window_length=window, polyorder=po, mode="interp")
    return arr


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


def fmt(val, p: int = 4) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "—"
    return f"{val:.{p}g}"


def _is_float(v: str) -> bool:
    try:
        float(str(v).strip().replace(",", "."))
        return True
    except ValueError:
        return False


def _eff_t_start(row) -> float | None:
    """Effective t_start: t_end − avg_duration if set (> 0), otherwise t_start."""
    ad = row.get("avg_duration")
    if pd.notna(ad) and float(ad) > 0 and pd.notna(row.get("t_end")):
        return float(row["t_end"]) - float(ad)
    v = row.get("t_start")
    return float(v) if pd.notna(v) else None


def _amp_label(filename: str, ch_name: str, multi: bool) -> str:
    """Composite (file, channel) label — bare channel name when only one file is loaded."""
    return f"{filename} · {ch_name}" if multi else ch_name


def parse_potentiostat_csv(raw: str, sep: str, mode: str = "amperometry") -> tuple[pd.DataFrame, list[dict]]:
    """
    Parse multi-channel potentiostat exports (Bio-Logic, CH Instruments, etc.).

    Format assumed:
        • N metadata rows (Date, Notes, blank, …)
        • One channel-label row: 'CH1: …', '', 'CH2: …', '', …
        • Zero or more non-numeric rows (measurement date, etc.)
        • One units row: 's', 'µA', 's', 'µA', …  (or 'V', 'µA' for CV)
        • Numeric data rows

    mode='amperometry': returns channels with {name, tc, ic} (time + current)
    mode='cv':          returns channels with {name, vc, ic} (voltage + current)
    """
    import re as _re
    engine = "python" if sep == r"\s+" else "c"
    _split = _re.compile(sep).split if sep == r"\s+" else lambda ln: ln.split(sep)
    max_cols = max(
        (len(_split(ln.strip())) for ln in raw.splitlines() if ln.strip()),
        default=1,
    )
    all_df = pd.read_csv(
        io.StringIO(raw), sep=sep, header=None, dtype=str,
        engine=engine, skipinitialspace=True,
        names=range(max_cols),
    )
    all_df.columns = list(range(all_df.shape[1]))

    def row_numeric(row) -> bool:
        vals = [v for v in row if pd.notna(v) and str(v).strip() not in ("", "nan")]
        return bool(vals) and all(_is_float(v) for v in vals)

    data_start = next(
        (i for i, (_, r) in enumerate(all_df.iterrows()) if row_numeric(r)),
        None,
    )
    if data_start is None:
        raise ValueError("No numeric data rows found — check delimiter.")

    units_row = all_df.iloc[data_start - 1] if data_start >= 1 else None

    # Find channel-label row: highest row before data containing 'CH'
    ch_row = None
    for i in range(data_start - 2, -1, -1):
        cells = [str(v).strip() for v in all_df.iloc[i]
                 if pd.notna(v) and str(v).strip() not in ("", "nan")]
        if any("CH" in c or "channel" in c.lower() for c in cells):
            ch_row = all_df.iloc[i]
            break

    # Build column names: "CH1 (s)", "CH1 (µA)", "CH2 (s)", …
    n_cols = all_df.shape[1]
    col_names: list[str] = []
    last_ch = "CH"
    for c in range(n_cols):
        if ch_row is not None:
            cell = str(ch_row.iloc[c]).strip()
            if cell and cell not in ("nan", ""):
                last_ch = cell.split(":")[0].strip()
        unit = (str(units_row.iloc[c]).strip()
                if units_row is not None else str(c))
        if unit in ("nan", ""):
            unit = str(c)
        col_names.append(f"{last_ch} ({unit})")

    data = all_df.iloc[data_start:].copy()
    data.columns = col_names
    data = data.apply(
        lambda s: pd.to_numeric(s.str.replace(",", "."), errors="coerce")
    )
    df = data.reset_index(drop=True)

    # Auto-infer channel pairs from column names
    _cv_mode = (mode == "cv")
    _x_units = (
        {"v", "mv", "volt", "volts", "potential", "e/v", "e / v"}
        if _cv_mode else
        {"s", "sec", "seconds", "ms", "min"}
    )
    _x_key = "vc" if _cv_mode else "tc"
    _current_units = {"µa", "ua", "na", "ma", "a", "µA", "nA", "mA"}
    from collections import defaultdict
    groups: dict[str, dict] = defaultdict(dict)
    for col in df.columns:
        if " (" in col and col.endswith(")"):
            prefix = col[: col.rfind(" (")]
            unit   = col[col.rfind(" (") + 2 : -1]
            if unit.lower() in _x_units:
                groups[prefix][_x_key] = col
            elif unit.lower() in _current_units or unit in _current_units:
                groups[prefix]["ic"] = col
    channels = [
        {"name": name, _x_key: m[_x_key], "ic": m["ic"]}
        for name, m in groups.items()
        if _x_key in m and "ic" in m
    ]

    return df, channels


def _ps_unit(raw: str | None, fallback: str) -> str:
    """Extract bare unit from strings like 'Time / s' → 's'."""
    if not raw:
        return fallback
    s = raw.strip()
    return s.split("/")[-1].strip() if "/" in s else s


def parse_pssession(file_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    """
    Parse a PalmSens .pssession file (ZIP archive containing XML).
    Returns (df, channels) compatible with the rest of the app.

    Tries three common XML layouts used across PSTrace versions:
      1. <Curve> with <Point X="…" Y="…"/> children
      2. <DataSet> with <Time> and <I> text lists
      3. <Values> with <Value>t,i</Value> pairs
    """
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        with zf:
            xml_files = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            target = xml_files[0] if xml_files else (zf.namelist() or [None])[0]
            if target is None:
                raise ValueError("Empty .pssession archive.")
            with zf.open(target) as f:
                root = ET.parse(f).getroot()
    except zipfile.BadZipFile:
        # Older PSTrace versions save plain XML directly
        try:
            root = ET.fromstring(file_bytes)
        except ET.ParseError as e:
            raise ValueError(
                f".pssession is neither a ZIP archive nor valid XML: {e}"
            )

    # (name, x_unit, y_unit, times, currents)
    records: list[tuple[str, str, str, list, list]] = []

    # Strategy 1: <Curve> elements with <Point X="…" Y="…"/>
    for i, curve in enumerate(root.findall(".//Curve")):
        x_unit = _ps_unit(curve.findtext("XUnit") or curve.findtext("XTitle"), "s")
        y_unit = _ps_unit(curve.findtext("YUnit") or curve.findtext("YTitle"), "µA")
        points = curve.findall(".//Point")
        if points and "X" in points[0].attrib:
            try:
                times    = [float(p.get("X", "nan")) for p in points]
                currents = [float(p.get("Y", "nan")) for p in points]
                name = curve.get("Title") or curve.get("Name") or f"CH{i + 1}"
                records.append((name, x_unit, y_unit, times, currents))
            except ValueError:
                pass

    # Strategy 2: <DataSet> with separate <Time>/<I> whitespace-delimited text
    if not records:
        for i, ds in enumerate(root.findall(".//DataSet")):
            t_el = ds.find("Time") or ds.find("T")
            i_el = ds.find("I") or ds.find("Current")
            if t_el is not None and i_el is not None and t_el.text and i_el.text:
                try:
                    times    = [float(v) for v in t_el.text.split()]
                    currents = [float(v) for v in i_el.text.split()]
                    records.append((f"CH{i + 1}", "s", "µA", times, currents))
                except ValueError:
                    pass

    # Strategy 3: <Values> → <Value>t,i</Value> comma-separated pairs
    if not records:
        for i, vel in enumerate(root.findall(".//Values")):
            rows_xy = []
            for v in vel.findall("Value"):
                txt = (v.text or "").strip()
                if "," in txt:
                    try:
                        a, b = txt.split(",", 1)
                        rows_xy.append((float(a), float(b)))
                    except ValueError:
                        pass
            if rows_xy:
                times, currents = zip(*rows_xy)
                records.append((f"CH{i + 1}", "s", "µA", list(times), list(currents)))

    if not records:
        children = ", ".join(f"<{c.tag}>" for c in list(root)[:8])
        raise ValueError(
            f"Could not extract time/current data from this .pssession file. "
            f"Root element: <{root.tag}>, first children: {children}. "
            f"Share this info so the parser can be extended for your format version."
        )

    max_len = max(len(r[3]) for r in records)
    col_data: dict[str, np.ndarray] = {}
    channels: list[dict] = []
    for name, x_unit, y_unit, times, currents in records:
        t_col = f"{name} ({x_unit})"
        i_col = f"{name} ({y_unit})"
        t_arr = np.full(max_len, np.nan)
        t_arr[: len(times)] = times
        i_arr = np.full(max_len, np.nan)
        i_arr[: len(currents)] = currents
        col_data[t_col] = t_arr
        col_data[i_col] = i_arr
        channels.append({"name": name, "tc": t_col, "ic": i_col})

    return pd.DataFrame(col_data), channels


# ── PNG / vector rendering (matplotlib) ──────────────────────────────────────

_ORIGIN_RC = {
    "font.family":        "sans-serif",
    "font.size":          10,
    "axes.labelsize":     11,
    "axes.titlesize":     12,
    "xtick.labelsize":    10,
    "ytick.labelsize":    10,
    "legend.fontsize":    9,
    "legend.frameon":     True,
    "legend.framealpha":  1.0,
    "legend.edgecolor":   "black",
    "legend.fancybox":    False,
    "lines.linewidth":    1.5,
    "axes.linewidth":     1.0,
    "axes.edgecolor":     "black",
    "xtick.major.width":  1.0,
    "ytick.major.width":  1.0,
    "xtick.major.size":   5,
    "ytick.major.size":   5,
    "xtick.minor.width":  0.8,
    "ytick.minor.width":  0.8,
    "xtick.minor.size":   3,
    "ytick.minor.size":   3,
    "xtick.direction":    "in",
    "ytick.direction":    "in",
    "xtick.top":          True,
    "ytick.right":        True,
    "axes.grid":          False,
    "figure.facecolor":   "white",
    "axes.facecolor":     "white",
    "savefig.facecolor":  "white",
}

_MINIMAL_RC = {
    "font.family":       "sans-serif",
    "font.size":         8,
    "axes.labelsize":    9,
    "axes.titlesize":    10,
    "xtick.labelsize":   8,
    "ytick.labelsize":   8,
    "legend.fontsize":   7,
    "legend.framealpha": 0.9,
    "lines.linewidth":   1.2,
    "axes.linewidth":    0.8,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.major.size":  3,
    "ytick.major.size":  3,
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
    "savefig.facecolor": "white",
}

_MIME = {"png": "image/png", "svg": "image/svg+xml",
         "pdf": "application/pdf", "tiff": "image/tiff"}


def _apply_spine_style(ax, style: str) -> None:
    """Apply spine / tick style based on export style name."""
    if style == "origin":
        ax.minorticks_on()
        # all four spines already visible by default; ensure they're black
        for spine in ax.spines.values():
            spine.set_linewidth(1.0)
            spine.set_color("black")
    else:
        ax.spines[["top", "right"]].set_visible(False)


def render_ts_png(amp_files: list[dict], cpdf, cur_unit: str, visible: list[str],
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
        for _, row in cpdf.iterrows():
            _ets_png = _eff_t_start(row)
            if _ets_png is not None and pd.notna(row.get("t_end")):
                clr = "darkorange" if row.get("Baseline") else "steelblue"
                ax.axvspan(_ets_png, row["t_end"], alpha=0.10, color=clr)
                ylim = ax.get_ylim()
                ax.text(_ets_png + 0.5, ylim[1],
                        str(row["Label"]), fontsize=_afs, va="top", color=clr)
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
            # Same blank-exclusion as the in-app Plotly chart, kept in sync.
            _keep = [not (bool(b) if pd.notna(b) else False) for b in
                     res.get("baselines", [False] * len(res["concs"]))]
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


def find_cv_peaks(voltage: np.ndarray, current: np.ndarray,
                  prominence: float, distance: int,
                  width: int | None = None,
                  height: float | None = None) -> dict:
    """
    Detect anodic (local maxima) and cathodic (local minima) peaks in a CV trace.
    prominence : min height relative to surrounding baseline
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
    _kw: dict = dict(prominence=prominence, distance=max(1, distance))
    if width  is not None and width  > 0:  _kw["width"]  = width
    if height is not None and height > 0:  _kw["height"] = height
    anodic_idx,   _ = scipy.signal.find_peaks(i,  **_kw)
    cathodic_idx, _ = scipy.signal.find_peaks(-i, **_kw)
    return {
        "anodic":   [{"Ep": float(v[k]), "Ip": float(i[k])} for k in anodic_idx],
        "cathodic": [{"Ep": float(v[k]), "Ip": float(i[k])} for k in cathodic_idx],
    }


def render_cv_png(cv_df: pd.DataFrame, cv_channels: list[dict],
                  visible: list[str], volt_unit: str, cur_unit: str,
                  peaks_map: dict | None = None,
                  dpi: int = 150, fmt: str = "png",
                  figsize: tuple | None = None, style: str = "default") -> bytes:
    _rc  = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    _lfs = 9 if style == "minimal" else 11
    _lgfs = 7 if style == "minimal" else 8
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (9, 6))
        for j, ch in enumerate(cv_channels):
            if ch["name"] not in visible:
                continue
            v   = to_num(cv_df[ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
            i   = to_num(cv_df[ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            col = PAL[j % len(PAL)]
            ax.plot(v, i, color=col, label=ch["name"], linewidth=1.4)
            if peaks_map and ch["name"] in peaks_map:
                pk = peaks_map[ch["name"]]
                for p in pk.get("anodic", []):
                    ax.plot(p["Ep"], p["Ip"], "^", color=col, markersize=10, zorder=5,
                            label=f"{ch['name']} Ep,a={p['Ep']:.3g} {volt_unit}")
                for p in pk.get("cathodic", []):
                    ax.plot(p["Ep"], p["Ip"], "v", color=col, markersize=10, zorder=5,
                            label=f"{ch['name']} Ep,c={p['Ep']:.3g} {volt_unit}")
        ax.axhline(0, color="#bbbbbb", linewidth=0.8, linestyle="--")
        ax.set_xlabel(f"Potential ({volt_unit})", fontsize=_lfs)
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


# ─────────────────────────────────────────────────────────────────────────────
# Microplate / Assay helpers
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Session-state bootstrap
# ─────────────────────────────────────────────────────────────────────────────
SS = st.session_state

for _k, _v in [
    ("df", None),
    ("channels", []),
    ("amp_files", []),       # [{filename, df, channels}] — multi-file amperometry
    ("cal_results", None),
    ("ts_fig", None),
    ("cal_fig", None),
    ("ts_visible", []),
    ("conc_unit", "mM"),
    ("cur_unit", "µA"),
    ("vol_unit", "µL"),
    ("initial_volume", 1.0),
    ("smooth_method", "None"),
    ("smooth_window", 11),
    ("smooth_polyorder", 2),
    ("ts_y_auto", True),
    ("ts_y_min",  None),
    ("ts_y_max",  None),
    # Shared / CV
    ("mode",       "Amperometry"),
    ("volt_unit",  "V"),
    ("cv_cur_unit","µA"),
    ("cv_sr_unit", "mV/s"),
    ("cv_runs",    []),          # [{scan_rate, label, filename, df, channels, peaks}]
    # Assay
    ("assay_plate",     None),
    ("assay_sig_unit",  "Abs"),
    ("assay_conc_unit", "µM"),
    ("assay_std_res",   None),
]:
    if _k not in SS:
        SS[_k] = _v

if "assay_std_df" not in SS:
    SS["assay_std_df"] = pd.DataFrame({
        "Label": ["Blank", "Std 2", "Std 3", "Std 4", "Std 5", "Std 6", "Std 7", "Std 8"],
        "Conc":  [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
        "S1":    ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
        "S2":    ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"],
        "S3":    ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
    })

if "assay_sample_df" not in SS:
    SS["assay_sample_df"] = pd.DataFrame(
        {"Well": pd.Series([], dtype=str), "Label": pd.Series([], dtype=str)}
    )

if "cpdf" not in SS:
    SS.cpdf = pd.DataFrame({
        "Label":         ["Blank", "Step 1", "Step 2", "Step 3"],
        "Concentration": [0.0, 0.1, 0.5, 1.0],
        "Spike Vol":     [np.nan, np.nan, np.nan, np.nan],
        "Stock Conc":    [np.nan, np.nan, np.nan, np.nan],
        "t_start":       [0.0, 120.0, 300.0, 480.0],
        "t_end":         [60.0, 180.0, 360.0, 540.0],
        "avg_duration":  [np.nan, np.nan, np.nan, np.nan],
        "Baseline":      [True, False, False, False],
    })

def _apply_cfg_dict(d: dict) -> None:
    """Apply a loaded config dict (from localStorage or an imported JSON file) to session state."""
    if "conc_unit"       in d: SS.conc_unit       = d["conc_unit"]
    if "cur_unit"        in d: SS.cur_unit        = d["cur_unit"]
    if "volt_unit"       in d: SS.volt_unit       = d["volt_unit"]
    if "cv_cur_unit"     in d: SS.cv_cur_unit     = d["cv_cur_unit"]
    if "cv_sr_unit"      in d: SS.cv_sr_unit      = d["cv_sr_unit"]
    if "vol_unit"        in d: SS.vol_unit        = d["vol_unit"]
    if "initial_volume"  in d: SS.initial_volume  = float(d["initial_volume"])
    if "smooth_method"   in d: SS.smooth_method   = d["smooth_method"]
    if "smooth_window"   in d: SS.smooth_window   = int(d["smooth_window"])
    if "smooth_polyorder" in d: SS.smooth_polyorder = int(d["smooth_polyorder"])
    if "assay_sig_unit"  in d: SS.assay_sig_unit  = d["assay_sig_unit"]
    if "assay_conc_unit" in d: SS.assay_conc_unit = d["assay_conc_unit"]
    if "calibration_points" in d:
        _cp = pd.DataFrame(d["calibration_points"])
        for _col in ["Concentration", "Spike Vol", "Stock Conc", "t_start", "t_end", "avg_duration"]:
            if _col in _cp.columns:
                _cp[_col] = pd.to_numeric(_cp[_col], errors="coerce")
        if "avg_duration" not in _cp.columns:
            _cp["avg_duration"] = np.nan
        if "Spike Vol" not in _cp.columns:
            _cp["Spike Vol"] = np.nan
        if "Stock Conc" not in _cp.columns:
            _cp["Stock Conc"] = np.nan
        if "Baseline" in _cp.columns:
            _cp["Baseline"] = _cp["Baseline"].astype(bool)
        SS.cpdf = _cp


# Auto-load saved config from the browser's localStorage once per session.
# The component's value arrives asynchronously, so retry across a couple of
# reruns before giving up (e.g. first-time users with nothing saved yet).
if not SS.get("config_loaded"):
    _raw_cfg = _local_storage.getItem("sensor_config")
    if _raw_cfg:
        try:
            _apply_cfg_dict(_json.loads(_raw_cfg))
        except Exception:
            pass
        SS["config_loaded"] = True
    else:
        SS["_cfg_load_tries"] = SS.get("_cfg_load_tries", 0) + 1
        if SS["_cfg_load_tries"] >= 5:
            SS["config_loaded"] = True
        else:
            time.sleep(0.2)
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar · Save / Load configuration
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Sensor Analysis Studio")
    st.radio("Section", ["Amperometry", "Cyclic Voltammetry", "Assay"], key="mode")
    st.divider()
    st.subheader("Configuration")

    _cfg = {
        "conc_unit":          SS.conc_unit,
        "cur_unit":           SS.cur_unit,
        "volt_unit":          SS.volt_unit,
        "cv_cur_unit":        SS.cv_cur_unit,
        "cv_sr_unit":         SS.cv_sr_unit,
        "vol_unit":           SS.vol_unit,
        "initial_volume":     SS.initial_volume,
        "smooth_method":      SS.smooth_method,
        "smooth_window":      SS.smooth_window,
        "smooth_polyorder":   SS.smooth_polyorder,
        "assay_sig_unit":     SS.assay_sig_unit,
        "assay_conc_unit":    SS.assay_conc_unit,
        "calibration_points": SS.cpdf.to_dict(orient="records"),
    }

    # ── Save to browser localStorage ──────────────────────────────────────────
    if st.button("Save", type="primary", use_container_width=True,
                 help="Saves in this browser — auto-loads next time you open the app here"):
        _local_storage.setItem("sensor_config", _json.dumps(_cfg, default=str))
        SS["_cfg_saved_at"] = time.strftime("%d %b %Y  %H:%M")
        st.toast("Configuration saved.", icon="✅")

    if SS.get("_cfg_saved_at"):
        st.caption(f"Last saved: {SS['_cfg_saved_at']}")
    else:
        st.caption("No saved config yet — click Save above.")

    st.divider()

    # ── Export / Import (for sharing or backup across machines) ────────────────
    with st.expander("Export / Import JSON"):
        st.download_button(
            "Export as JSON",
            data=_json.dumps(_cfg, indent=2, default=str).encode(),
            file_name="sensor_config.json",
            mime="application/json",
            use_container_width=True,
        )
        _cfg_up = st.file_uploader(
            "Import JSON",
            type=["json"],
            key="cfg_uploader",
            help="Load a config saved on another machine or shared by a colleague.",
        )
        if _cfg_up is not None:
            try:
                _loaded = _json.loads(_cfg_up.read())
                _apply_cfg_dict(_loaded)
                _local_storage.setItem("sensor_config", _json.dumps(_loaded, default=str))
                SS["_cfg_saved_at"] = time.strftime("%d %b %Y  %H:%M")
                st.success("Imported.")
            except Exception as _exc:
                st.error(f"Failed: {_exc}")

    st.divider()
    st.caption("Sensor Analysis Studio")


# ─────────────────────────────────────────────────────────────────────────────
# Title & tabs
# ─────────────────────────────────────────────────────────────────────────────
st.title("Sensor Analysis Studio")

# ─────────────────────────────────────────────────────────────────────────────
# CYCLIC VOLTAMMETRY section
# st.stop() at the end prevents the amperometry code below from executing.
# ─────────────────────────────────────────────────────────────────────────────
if SS.mode == "Cyclic Voltammetry":
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
                                          required=True,
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
            int(_dpi) if _fmt not in ["SVG", "PDF"] else 300,
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
                                "peaks":     {},
                            })
                        except Exception as _exc_fup:
                            _errs_new.append(f"{_fup.name}: {_exc_fup}")
                    for _e in _errs_new:
                        st.error(_e)
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
            st.plotly_chart(_fig_cvp, use_container_width=True,
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
                            "Anodic peaks":                  len(_na3),
                            f"Main Ep,a ({SS.volt_unit})":   fmt(_pa3["Ep"]) if _pa3 else "—",
                            f"Main Ip,a ({SS.cv_cur_unit})": fmt(_pa3["Ip"]) if _pa3 else "—",
                            "Cathodic peaks":                len(_nc3),
                            f"Main Ep,c ({SS.volt_unit})":   fmt(_pc3["Ep"]) if _pc3 else "—",
                            f"Main Ip,c ({SS.cv_cur_unit})": fmt(_pc3["Ip"]) if _pc3 else "—",
                        })
                if _psumm3:
                    with st.expander("Peak count summary", expanded=True):
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
                for _ci4, _chn4 in enumerate(_sel_chs4):
                    _col4 = PAL[_ci4 % len(PAL)]
                    _d4   = _ch4_data[_chn4]
                    _nu4  = _d4["scan_rate"].values
                    _dep4 = _d4["delta_Ep"].values
                    _vdep = np.isfinite(_dep4)
                    if not _vdep.any():
                        continue
                    _fig_dep_nu.add_trace(go.Scatter(
                        x=_nu4[_vdep], y=_dep4[_vdep],
                        name=_chn4, mode="markers+lines",
                        marker=dict(symbol="circle", size=10, color=_col4,
                                    line=dict(width=1, color="white")),
                        line=dict(color=_col4),
                    ))
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

    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# ASSAY section  (only reached when mode == "Assay")
# ─────────────────────────────────────────────────────────────────────────────
if SS.mode == "Assay":
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
        _a1_edited = st.data_editor(
            _a1_init.reset_index(),
            key="assay_plate_editor",
            use_container_width=True,
            hide_index=True,
            column_config={"Row": st.column_config.TextColumn("Row", disabled=True)},
        )
        if st.button("Apply manual values", key="assay_apply_manual"):
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

            if st.button("Apply layout", type="primary", key="assay_apply_std"):
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

    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# AMPEROMETRY section  (only reached when mode == "Amperometry")
# ─────────────────────────────────────────────────────────────────────────────
T1, T2, T3, T4 = st.tabs([
    "① Import & Configure", "② Time Series", "③ Calibration Curve", "④ Export",
])


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 · Import & Configure
# ═════════════════════════════════════════════════════════════════════════════
with T1:
    with st.expander("Quick-start guide", expanded=False):
        st.markdown("""
**Typical workflow:**

1. **Import & Configure** — upload your CSV/TXT file, map each column pair (time + current) to a named channel, set your concentration and current units.
2. **Time Series** — inspect the raw current traces. Use this to identify the time windows where each concentration was applied.
3. **Calibration Curve** — fill in the calibration table (one row per concentration step), click *Compute*, choose a fit type, and review the statistics.
4. **Export** — download the calibration CSV, plots (PNG or interactive HTML), or the raw data.

> **Tip:** Calibration windows are shown as shaded bands on the time-series chart so you can visually verify your time entries.
""")

    st.subheader("Upload File(s)")
    ups = st.file_uploader(
        "Drag and drop one or more raw sensor data files here, or click to browse",
        type=["csv", "txt", "pssession"],
        accept_multiple_files=True,
        help=(
            "Supports comma-, tab-, semicolon-, or space-delimited files with a header row. "
            "Upload multiple files to compare across runs/sensors — each file gets its own "
            "column mapping below."
        ),
    )

    def _parse_one_file(_up, _fi: int) -> tuple[pd.DataFrame | None, list[dict]]:
        """Parse one uploaded file, returning (df, auto_channels)."""
        if _up.name.lower().endswith(".pssession"):
            _df, _auto = parse_pssession(_up.read())
            return _df, _auto

        _raw_bytes = _up.read()
        if _raw_bytes[:2] in (b"\xff\xfe", b"\xfe\xff"):
            _raw = _raw_bytes.decode("utf-16")
        else:
            _raw = _raw_bytes.decode("utf-8", errors="replace")

        _file_fmt = st.selectbox(
            "File format",
            ["Standard CSV", "Multi-channel instrument (potentiostat, etc.)"],
            help=(
                "Choose **Multi-channel instrument** for files exported from Bio-Logic, "
                "CH Instruments, Autolab, or similar — they have metadata rows, "
                "channel-label rows, and a units row above the numeric data."
            ),
            key=f"file_fmt_{_fi}",
        )

        _c1, _c2 = st.columns(2)
        _delim_label = _c1.selectbox(
            "Delimiter",
            ["Auto-detect", "Comma  ,", "Tab  \\t", "Semicolon  ;", "Space"],
            help="Choose the character that separates columns. Auto-detect works for most files.",
            key=f"delim_{_fi}",
        )
        _skip = int(_c2.number_input(
            "Rows to skip before header", 0, 50, 0,
            help="Only applies to Standard CSV mode. Multi-channel mode finds the data start automatically.",
            key=f"skip_{_fi}",
        ))

        _dmap = {
            "Auto-detect": None,
            "Comma  ,": ",", "Tab  \\t": "\t",
            "Semicolon  ;": ";", "Space": r"\s+",
        }
        _d = _dmap[_delim_label]
        if _d is None:
            _lines = _raw.splitlines()
            _sniff_line = _lines[_skip] if _skip < len(_lines) else (_lines[0] if _lines else "")
            _d = next((c for c in [",", "\t", ";"] if c in _sniff_line), r"\s+")

        _engine = "python" if _d == r"\s+" else "c"

        if _file_fmt.startswith("Multi-channel"):
            _df, _auto = parse_potentiostat_csv(_raw, _d)
            return _df, _auto
        _df = pd.read_csv(
            io.StringIO(_raw), sep=_d, skiprows=_skip,
            engine=_engine, skipinitialspace=True,
        )
        _df.columns = [c.lstrip("﻿").strip() for c in _df.columns]
        return _df, []

    if ups:
        _existing_by_name = {f["filename"]: f for f in SS.amp_files}
        _parsed_files = []
        for _fi, _up in enumerate(ups):
            with st.expander(f"📄 {_up.name}", expanded=(len(ups) <= 3)):
                try:
                    _df, _auto_channels = _parse_one_file(_up, _fi)
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
                    help="Each channel corresponds to one electrode. Most files have pairs of (time, current) columns.",
                    key=f"n_ch_{_fi}",
                ))

                _ha, _hb, _hc = st.columns([2, 3, 3])
                _ha.markdown("**Channel name**")
                _hb.markdown("**Time column**")
                _hc.markdown("**Current column**")

                def _col_idx(col: str, _cols=_all_cols) -> int:
                    return _cols.index(col) if col in _cols else 0

                _new_chs = []
                for _i in range(_n_ch):
                    _preset = _preset_chs[_i] if _i < len(_preset_chs) else {}
                    _ca, _cb, _cc = st.columns([2, 3, 3])
                    _name = _ca.text_input(
                        "nm", _preset.get("name", f"Channel {_i + 1}"),
                        key=f"n{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _tc = _cb.selectbox(
                        "tc", _all_cols,
                        index=_col_idx(_preset.get("tc", _all_cols[min(_i * 2, len(_all_cols) - 1)])),
                        key=f"tc{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _ic = _cc.selectbox(
                        "ic", _all_cols,
                        index=_col_idx(_preset.get("ic", _all_cols[min(_i * 2 + 1, len(_all_cols) - 1)])),
                        key=f"ic{_fi}_{_i}", label_visibility="collapsed",
                    )
                    _new_chs.append({"name": _name, "tc": _tc, "ic": _ic})

                _parsed_files.append({"filename": _up.name, "df": _df, "channels": _new_chs})

        if _parsed_files and st.button("Apply Channel Configuration", type="primary"):
            SS.amp_files = _parsed_files
            # Keep SS.df/SS.channels as an alias to the first file for any
            # legacy single-file consumers.
            SS.df       = _parsed_files[0]["df"]
            SS.channels = _parsed_files[0]["channels"]
            SS.ts_visible = []
            st.success(
                f"{len(_parsed_files)} file(s), "
                f"{sum(len(f['channels']) for f in _parsed_files)} channel(s) saved. "
                "Head to the **Time Series** tab to inspect your traces."
            )

    if SS.amp_files:
        st.divider()
        st.subheader("Units")
        st.caption("These labels appear on all plot axes and in the statistics table.")
        u1, u2 = st.columns(2)
        SS.conc_unit = u1.text_input("Concentration unit", SS.conc_unit,
                                      help="e.g. mM, µM, ppm, ng/mL")
        SS.cur_unit  = u2.text_input("Current unit", SS.cur_unit,
                                      help="e.g. µA, nA, mA")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 · Time Series
# ═════════════════════════════════════════════════════════════════════════════
_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]

with T2:
    if not SS.amp_files:
        st.info("Complete the **Import & Configure** step first.")
    else:
        _multi_file = len(SS.amp_files) > 1

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
            )
            if SS.smooth_method != "None":
                SS.smooth_window = int(sm2.number_input(
                    "Window (samples)", min_value=3, value=int(SS.smooth_window), step=2,
                    help="Odd number of samples in the smoothing window.",
                ))
                if SS.smooth_method == "Savitzky-Golay":
                    SS.smooth_polyorder = int(sm3.number_input(
                        "Polynomial order", min_value=1, max_value=5,
                        value=int(SS.smooth_polyorder),
                        help="Must be less than the window size.",
                    ))

        # All (file, channel) combos available across loaded files.
        _combos = [
            (fi, ci, frec["filename"], frec["df"], ch)
            for fi, frec in enumerate(SS.amp_files)
            for ci, ch in enumerate(frec["channels"])
        ]
        _all_ch_names = [_amp_label(fn, ch["name"], _multi_file) for _, _, fn, _, ch in _combos]
        _vis_key = "ts_vis_ms"
        # Reset multiselect state if channels have changed since last config apply
        if _vis_key not in SS or any(c not in _all_ch_names for c in SS.get(_vis_key, [])):
            SS[_vis_key] = _all_ch_names[:]

        # Solo / isolate row (only useful with 2+ combos)
        if len(_combos) >= 2:
            _iso_cols = st.columns([1.4] + [1] * len(_combos))
            _iso_cols[0].markdown("**Isolate:**", help="Click a name to show only that trace")
            for _j, _lbl in enumerate(_all_ch_names):
                if _iso_cols[_j + 1].button(
                    _lbl, key=f"ts_solo_{_j}",
                    use_container_width=True,
                    help=f"Show only {_lbl}",
                ):
                    SS[_vis_key] = [_lbl]

        sel = st.multiselect(
            "Visible channels",
            _all_ch_names,
            key=_vis_key,
        )
        SS.ts_visible = sel

        with st.expander("Y-axis range", expanded=False):
            _y_auto = st.checkbox("Auto-scale", value=SS.ts_y_auto, key="ts_y_auto_cb")
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
                    key="ts_y_min_ni", help=_range_help,
                ))
                SS.ts_y_max = float(_yc2.number_input(
                    "Y max", value=float(_def_max), format="%.6g", step=0.0001,
                    key="ts_y_max_ni", help=_range_help,
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
        for _, row in SS.cpdf.iterrows():
            _ets2 = _eff_t_start(row)
            if _ets2 is not None and pd.notna(row.get("t_end")):
                clr = ("rgba(255,165,0,0.22)"
                       if row.get("Baseline") else "rgba(100,160,255,0.15)")
                fig_ts.add_vrect(
                    x0=_ets2, x1=row["t_end"],
                    fillcolor=clr, layer="below", line_width=0,
                    annotation_text=str(row["Label"]),
                    annotation_position="top left",
                    annotation=dict(font_size=10, font_color=_pt_ts["annot_font"]),
                )

        fig_ts.update_layout(
            xaxis_title="Time (s)",
            yaxis_title=f"Current ({SS.cur_unit})",
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
                                "modeBarButtonsToRemove": ["select2d", "lasso2d"]})
        SS.ts_fig = fig_ts

        dl1, dl2 = st.columns(2)
        dl1.download_button(
            "Download as interactive HTML",
            data=fig_ts.to_html(include_plotlyjs="cdn"),
            file_name="time_series.html",
            mime="text/html",
        )
        ts_png = render_ts_png(SS.amp_files, SS.cpdf, SS.cur_unit, sel,
                               smooth_method=SS.smooth_method,
                               smooth_window=SS.smooth_window,
                               smooth_polyorder=SS.smooth_polyorder)
        dl2.download_button(
            "Download as PNG",
            data=ts_png,
            file_name="time_series.png",
            mime="image/png",
        )


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 · Calibration Curve
# ═════════════════════════════════════════════════════════════════════════════
with T3:
    if not SS.amp_files:
        st.info("Complete the **Import & Configure** step first.")
    else:
        # ── Calibration-point editor ──────────────────────────────────────
        st.subheader("Calibration Points")
        st.caption(
            "Add one row per concentration step. "
            "**t start / t end** define the averaging window — read these off the "
            "time-series chart. "
            "Check **Baseline?** on the blank or buffer row; its average current "
            "is subtracted from all other steps. "
            "**Spike Vol / Stock Conc** are optional — fill them in to use the "
            "effective concentration calculator below instead of typing "
            "Concentration by hand."
        )
        if "cal_editor_version" not in SS:
            SS.cal_editor_version = 0
        _cpdf_edit = st.data_editor(
            SS.cpdf,
            key=f"cal_editor_{SS.cal_editor_version}",
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

        with st.expander("Effective concentration calculator (serial dilution)"):
            st.caption(
                "Models a single vessel that starts at **Initial Volume** of blank "
                "buffer. Each row's **Spike Vol** of **Stock Conc** analyte is added "
                "in sequence (top to bottom); clicking the button below computes "
                "the cumulative, dilution-corrected concentration after each "
                "addition and writes it into the **Concentration** column above. "
                "**t start** is filled in from **Avg window (s)** at the same time, "
                "for any row where that column is set. Blank Spike Vol / Stock Conc "
                "cells are treated as 0."
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
            if st.button("Update Concentration & t start"):
                _calc_df = _cpdf_edit.copy()
                if _calc_df[["Spike Vol", "Stock Conc"]].notna().any().any():
                    _vol  = float(SS.initial_volume)
                    _mass = 0.0
                    _eff  = []
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
                SS.cpdf = _calc_df
                SS.cal_editor_version += 1
                st.success("Concentration / t start updated above.")
                st.rerun()

        SS.cpdf = _cpdf_edit
        st.divider()

        # ── Analysis settings ─────────────────────────────────────────────
        st.subheader("Analysis Settings")
        if SS.smooth_method != "None":
            st.caption(
                f"Averaging below uses the **{SS.smooth_method}** smoothing "
                "configured in the Time Series tab."
            )
        _cal_multi_file = len(SS.amp_files) > 1
        _cal_combo_lookup = {
            _amp_label(frec["filename"], ch["name"], _cal_multi_file): (frec["df"], ch)
            for frec in SS.amp_files
            for ch in frec["channels"]
        }
        a1, a2, a3 = st.columns(3)
        analyze_chs = a1.multiselect(
            "Channels to analyse",
            list(_cal_combo_lookup.keys()),
            default=list(_cal_combo_lookup.keys())[:1],
            help="Select one or more channels (and, with multiple files loaded, file·channel pairs). "
                 "Each gets its own calibration curve, computed over the shared time windows above.",
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

        if st.button("Compute Calibration", type="primary"):
            cpdf = (SS.cpdf
                    .dropna(subset=["t_end"])
                    .reset_index(drop=True))
            if cpdf.empty:
                st.error("No valid rows — fill in the calibration table above.")
                st.stop()

            base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
            base_idx  = int(base_rows.index[0]) if len(base_rows) else 0
            if len(base_rows) == 0:
                st.warning("No baseline row marked — using the first row as baseline.")

            results = {}
            for ch_name in analyze_chs:
                df, ch = _cal_combo_lookup[ch_name]
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
            if show_avg and len(analyze_chs) >= 2:
                all_di    = np.array([results[c]["delta_i"] for c in analyze_chs],
                                     dtype=float)
                all_avgs  = np.array([results[c]["avgs"]    for c in analyze_chs],
                                     dtype=float)
                all_sigma = [results[c]["sigma_bl"] for c in analyze_chs]
                n_ch      = len(analyze_chs)

                avg_delta_i   = np.nanmean(all_di, axis=0)
                std_across_ch = np.nanstd(all_di, axis=0, ddof=1)  # inter-channel spread (sample std)
                avg_avgs      = np.nanmean(all_avgs, axis=0)
                # propagated blank noise: sqrt(Σσi²) / n_ch (all channels, not just those with finite σ)
                _valid_s = [s for s in all_sigma if np.isfinite(s)]
                sigma_bl_avg = (np.sqrt(sum(s**2 for s in _valid_s)) / n_ch
                                if _valid_s else np.nan)

                results["Channel Average"] = dict(
                    concs      = results[analyze_chs[0]]["concs"],
                    labels     = results[analyze_chs[0]]["labels"],
                    avgs       = avg_avgs.tolist(),
                    sigs       = std_across_ch.tolist(),
                    delta_i    = avg_delta_i.tolist(),
                    sigma_bl   = float(sigma_bl_avg),
                    is_average = True,
                    baselines  = results[analyze_chs[0]]["baselines"],
                )

            SS.cal_results = dict(
                results=results, fit_type=fit_type, n_seg=n_seg
            )
            st.success("Calibration computed — results below.")

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
                _keep  = [not bool(b) for b in
                          res.get("baselines", [False] * len(res["concs"]))]
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
            st.plotly_chart(fig_cal, use_container_width=True,
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
        if SS.ts_fig is not None:
            dl5.download_button(
                "Plot — interactive HTML",
                data=SS.ts_fig.to_html(include_plotlyjs="cdn"),
                file_name="time_series.html",
                mime="text/html",
            )
            ts_vis = SS.ts_visible if SS.ts_visible else all_ch_names_export
            ts_png_bytes = render_ts_png(
                SS.amp_files, SS.cpdf, SS.cur_unit, ts_vis,
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
                "DPI", options=[150, 300, 600], default=300, required=True, key="pub_dpi",
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
        _pdpi_val = int(_pdpi) if _pfmt not in ["SVG", "PDF"] else 300
        _pfmt_l   = _pfmt.lower()
        _pstyle_l = _pstyle.lower()

        _pa, _pb = st.columns(2)
        if SS.amp_files:
            _ts_vis  = SS.ts_visible or [
                _amp_label(f["filename"], c["name"], len(SS.amp_files) > 1)
                for f in SS.amp_files for c in f["channels"]
            ]
            _prev_ts = render_ts_png(SS.amp_files, SS.cpdf, SS.cur_unit, _ts_vis,
                                     dpi=96, fmt="png", figsize=_pfs, style=_pstyle_l,
                                     smooth_method=SS.smooth_method,
                                     smooth_window=SS.smooth_window,
                                     smooth_polyorder=SS.smooth_polyorder)
            _pub_ts  = render_ts_png(SS.amp_files, SS.cpdf, SS.cur_unit, _ts_vis,
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


