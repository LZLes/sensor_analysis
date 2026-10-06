"""Time-series helpers shared by the trace modes (Amperometry, Solid-State):
channel labels and the time-series export figure."""

import io

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from core.constants import PAL
from core.numeric import _eff_t_start, smooth_signal, to_num
from core.plotting import _MINIMAL_RC, _ORIGIN_RC, _apply_spine_style


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


def window_stats(t: np.ndarray, y: np.ndarray, cpdf: pd.DataFrame,
                 override_col: str | None = None) -> tuple[list[float], list[float]]:
    """Mean and SD (ddof=1) of `y` inside each calibration row's window,
    [effective t_start, t_end]; NaN where a window is unset or holds no data.
    A non-blank `override_col` value (Solid-State's manual Reading_mV)
    replaces that row's mean, with no SD."""
    avgs, sigs = [], []
    for _, row in cpdf.iterrows():
        if override_col and pd.notna(row.get(override_col)):
            avgs.append(float(row[override_col]))
            sigs.append(np.nan)
            continue
        ets = _eff_t_start(row)
        if ets is None or pd.isna(row.get("t_end")):
            avgs.append(np.nan)
            sigs.append(np.nan)
            continue
        pts = y[(t >= ets) & (t <= row["t_end"])]
        pts = pts[~np.isnan(pts)]
        avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
        sigs.append(float(np.std(pts, ddof=1)) if pts.size >= 2 else np.nan)
    return avgs, sigs
