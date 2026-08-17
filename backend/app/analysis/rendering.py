"""Publication-quality matplotlib renderers (PNG/SVG/PDF/TIFF), headless.

Interactive charting lives in the React frontend (Plotly.js) — these
renderers are only for the "download a static, publication-ready image"
export feature, which needs no browser/JS runtime.
"""
from __future__ import annotations

import io

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")   # headless backend — no display required
import matplotlib.pyplot as plt  # noqa: E402

from .calibration import eff_t_start
from .common import AVG_COLOR, PAL, amp_label
from .regression import piecewise_fit
from .signal import smooth_signal

MIME = {"png": "image/png", "svg": "image/svg+xml",
        "pdf": "application/pdf", "tiff": "image/tiff"}

ORIGIN_RC = {
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 9,
    "legend.frameon": True,
    "legend.framealpha": 1.0,
    "legend.edgecolor": "black",
    "legend.fancybox": False,
    "lines.linewidth": 1.5,
    "axes.linewidth": 1.0,
    "axes.edgecolor": "black",
    "xtick.major.width": 1.0,
    "ytick.major.width": 1.0,
    "xtick.major.size": 5,
    "ytick.major.size": 5,
    "xtick.minor.width": 0.8,
    "ytick.minor.width": 0.8,
    "xtick.minor.size": 3,
    "ytick.minor.size": 3,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.top": True,
    "ytick.right": True,
    "axes.grid": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
}

MINIMAL_RC = {
    "font.family": "sans-serif",
    "font.size": 8,
    "axes.labelsize": 9,
    "axes.titlesize": 10,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 7,
    "legend.framealpha": 0.9,
    "lines.linewidth": 1.2,
    "axes.linewidth": 0.8,
    "xtick.major.width": 0.8,
    "ytick.major.width": 0.8,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
}


def _apply_spine_style(ax, style: str) -> None:
    """Apply spine / tick style based on export style name."""
    if style == "origin":
        ax.minorticks_on()
        for spine in ax.spines.values():
            spine.set_linewidth(1.0)
            spine.set_color("black")
    else:
        ax.spines[["top", "right"]].set_visible(False)


def render_ts_png(amp_files: list[dict], cur_unit: str, visible: list[str],
                   dpi: int = 150, fmt: str = "png",
                   figsize: tuple | None = None, style: str = "default",
                   smooth_method: str = "None", smooth_window: int = 11,
                   smooth_polyorder: int = 2) -> bytes:
    rc = {"origin": ORIGIN_RC, "minimal": MINIMAL_RC}.get(style, {})
    lfs = 9 if style == "minimal" else 11
    lgfs = 7 if style == "minimal" else 9
    afs = 7 if style == "minimal" else 8
    multi = len(amp_files) > 1
    mpl_dashes = ["-", "--", ":", "-.", (0, (5, 1, 1, 1)), (0, (3, 1, 1, 1, 1, 1))]
    with matplotlib.rc_context(rc):
        fig, ax = plt.subplots(figsize=figsize or (13, 5))
        for fi, frec in enumerate(amp_files):
            for ci, ch in enumerate(frec["channels"]):
                lbl = amp_label(frec["filename"], ch["name"], multi)
                if lbl not in visible:
                    continue
                x = pd.to_numeric(frec["df"][ch["tc"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                yr = pd.to_numeric(frec["df"][ch["ic"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                y = smooth_signal(yr, smooth_method, smooth_window, smooth_polyorder)
                col = PAL[(fi if multi else ci) % len(PAL)]
                ls = mpl_dashes[ci % len(mpl_dashes)] if multi else "-"
                if smooth_method != "None":
                    ax.plot(x, yr, color=col, linewidth=0.6, linestyle=ls, alpha=0.30)
                ax.plot(x, y, color=col, label=lbl, linewidth=1.4, linestyle=ls)
        for frec in amp_files:
            for _, row in frec.get("cpdf", pd.DataFrame()).iterrows():
                ets = eff_t_start(row)
                if ets is not None and pd.notna(row.get("t_end")):
                    clr = "darkorange" if row.get("Baseline") else "steelblue"
                    ax.axvspan(ets, row["t_end"], alpha=0.10, color=clr)
                    ylim = ax.get_ylim()
                    lbl_txt = (f"{frec['filename']}: {row['Label']}"
                               if multi else str(row["Label"]))
                    ax.text(ets + 0.5, ylim[1], lbl_txt, fontsize=afs, va="top", color=clr)
        ax.set_xlabel("Time (s)", fontsize=lfs)
        ax.set_ylabel(f"Current ({cur_unit})", fontsize=lfs)
        ax.legend(fontsize=lgfs, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
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
    rc = {"origin": ORIGIN_RC, "minimal": MINIMAL_RC}.get(style, {})
    lfs = 9 if style == "minimal" else 11
    lgfs = 7 if style == "minimal" else 9
    afs = 6.5 if style == "minimal" else 7.5
    with matplotlib.rc_context(rc):
        fig, ax = plt.subplots(figsize=figsize or (8, 6))
        annot_blocks = []
        for j, (ch_name, res) in enumerate(res_map.items()):
            col = AVG_COLOR if res.get("is_average") else PAL[j % len(PAL)]
            keep = [not (bool(b) if pd.notna(b) else False) for b in
                    res.get("baselines", [False] * len(res["concs"]))]
            x = np.asarray(res["concs"], dtype=float)[keep]
            y = np.array(res["delta_i"], float)[keep]
            errs = [float(s) if (s and not np.isnan(s)) else 0.0
                    for s in np.asarray(res["sigs"], dtype=float)[keep]]
            marker = "D" if res.get("is_average") else "o"
            yerr = errs if res.get("is_average") else None
            ax.errorbar(x, y, yerr=yerr, fmt=marker, color=col, label=ch_name,
                        capsize=4, markersize=7, linewidth=1.4, elinewidth=1.2)
            pf = piecewise_fit(x, y, int(ns) if ft == "Segmented Linear" else 1)
            segs, breakpoints = pf["segments"], pf["breakpoints"]
            sigma_bl = float(res.get("sigma_bl", np.nan))
            ch_lines = [ch_name + ":"]
            for k, seg in enumerate(segs):
                xp = np.linspace(seg["xr"][0], seg["xr"][1], 300)
                yp = seg["slope"] * xp + seg["intercept"]
                ls = (0, (5, 2)) if res.get("is_average") else "--"
                ax.plot(xp, yp, linestyle=ls, color=col, linewidth=2)
                s, b, r2 = seg["slope"], seg["intercept"], seg["r2"]
                pfx = f"  seg {k + 1} " if len(segs) > 1 else "  "
                sign = "+" if b >= 0 else "−"
                ch_lines.append(f"{pfx}y = {s:.3g}x {sign} {abs(b):.3g}   R² = {r2:.4f}")
                if np.isfinite(sigma_bl) and s != 0:
                    lod = 3.0 * abs(sigma_bl) / abs(s)
                    loq = 10.0 * abs(sigma_bl) / abs(s)
                    ch_lines.append(
                        f"{pfx}Sens = {s:.3g} {cur_unit}/{conc_unit}"
                        f"   LOD = {lod:.3g}   LOQ = {loq:.3g} {conc_unit}"
                    )
            for bp in breakpoints:
                ax.axvline(bp, linestyle=":", color=col, linewidth=1.2)
                ax.annotate(f"{bp:.3g} {conc_unit}", xy=(bp, 1), xycoords=("data", "axes fraction"),
                            xytext=(2, -2), textcoords="offset points",
                            fontsize=afs, color=col, rotation=90, va="top", ha="left")
            annot_blocks.append("\n".join(ch_lines))
        ax.set_xlabel(f"Concentration ({conc_unit})", fontsize=lfs)
        ax.set_ylabel(f"ΔI ({cur_unit})", fontsize=lfs)
        ax.legend(fontsize=lgfs, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        if annot_blocks:
            ax.text(
                0.5, -0.22, "\n\n".join(annot_blocks),
                transform=ax.transAxes, fontsize=afs,
                va="top", ha="center", family="monospace",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                          alpha=0.88, edgecolor="#cccccc", linewidth=0.8),
            )
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_cv_png(cv_df: pd.DataFrame, cv_channels: list[dict],
                   visible: list[str], volt_unit: str, cur_unit: str,
                   peaks_map: dict | None = None,
                   dpi: int = 150, fmt: str = "png",
                   figsize: tuple | None = None, style: str = "default") -> bytes:
    rc = {"origin": ORIGIN_RC, "minimal": MINIMAL_RC}.get(style, {})
    lfs = 9 if style == "minimal" else 11
    lgfs = 7 if style == "minimal" else 8
    with matplotlib.rc_context(rc):
        fig, ax = plt.subplots(figsize=figsize or (9, 6))
        for j, ch in enumerate(cv_channels):
            if ch["name"] not in visible:
                continue
            v = pd.to_numeric(cv_df[ch["vc"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
            i = pd.to_numeric(cv_df[ch["ic"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
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
        ax.set_xlabel(f"Potential ({volt_unit})", fontsize=lfs)
        ax.set_ylabel(f"Current ({cur_unit})", fontsize=lfs)
        ax.legend(fontsize=lgfs, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_assay_curve(res: dict, show_reps: bool, conc_unit: str, sig_unit: str,
                        dpi: int = 150, fmt: str = "png",
                        figsize: tuple | None = None, style: str = "default") -> bytes:
    rc = {"origin": ORIGIN_RC, "minimal": MINIMAL_RC}.get(style, {})
    lfs = 9 if style == "minimal" else 11
    fit = res["fit"]
    cx = np.array(res["concs"], float)
    my = np.array(res["means"], float)
    sy = np.array(res["sds"], float)
    darr = np.array(res["delta_arr"], float)
    vm = np.isfinite(my) & np.isfinite(cx)
    with matplotlib.rc_context(rc):
        fig, ax = plt.subplots(figsize=figsize or (7, 5))
        if show_reps:
            for si, rc_col in enumerate([PAL[0], PAL[1], PAL[2]]):
                ry = darr[:, si]
                vr = np.isfinite(ry) & np.isfinite(cx)
                if vr.any():
                    ax.scatter(cx[vr], ry[vr], color=rc_col, s=22, alpha=0.6,
                               marker="o", facecolors="none", linewidths=1.2,
                               zorder=3, label=f"Set {si + 1}")
        ax.errorbar(cx[vm], my[vm], yerr=sy[vm], fmt="o", color="#4c96d7",
                    capsize=4, markersize=7, linewidth=1.4, elinewidth=1.2,
                    zorder=4, label="Mean")
        xp = np.linspace(max(0.0, cx[vm].min()), cx[vm].max(), 400)
        if fit["type"] == "linear":
            yp = fit["slope"] * xp + fit["intercept"]
            b = fit["intercept"]
            eq = (f"y = {fit['slope']:.3g}x {'+ ' if b >= 0 else '− '}{abs(b):.3g}"
                  f"\nR² = {fit['r2']:.4f}")
        elif fit["type"] == "quad":
            yp = fit["a"] * xp ** 2 + fit["b"] * xp + fit["c"]
            eq = (f"y = {fit['a']:.3g}x² + {fit['b']:.3g}x + {fit['c']:.3g}"
                  f"\nR² = {fit['r2']:.4f}")
        else:
            yp = fit["d"] + (fit["a"] - fit["d"]) / (1 + (xp / fit["c"]) ** fit["b"])
            eq = (f"4PL  a={fit['a']:.3g}  b={fit['b']:.3g}\n"
                  f"c={fit['c']:.3g}  d={fit['d']:.3g}  R²={fit['r2']:.4f}")
        ax.plot(xp, yp, "--", color="#ff9230", linewidth=2, label="Fit")
        ax.set_xlabel(f"Concentration ({conc_unit})", fontsize=lfs)
        ax.set_ylabel(f"ΔSignal ({sig_unit})", fontsize=lfs)
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        fig.tight_layout()
        ax.text(0.5, -0.22, eq, transform=ax.transAxes, fontsize=7,
                va="top", ha="center", family="monospace",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          alpha=0.88, edgecolor="#cccccc", linewidth=0.8))
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
