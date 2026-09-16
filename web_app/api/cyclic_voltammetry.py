"""
Cyclic Voltammetry mode API — ports macos_app/ui/modes/cyclic_voltammetry_view.py's
logic to HTTP endpoints. CV stays self-contained (inline CSV parsing, not
core/shared_tabs.py) for the same reason that view gave: modes/cyclic_voltammetry.py's
own docstring calls the duplication "pre-existing, not part of scope" to fix.

Import is a single step here rather than macos_app's preview-then-load flow:
a browser file upload can't be cheaply re-read the way a local path can, so
files are parsed with best-guess defaults immediately and usable right away,
matching the "import now, refine channel assignment after" model the other
two modes already use — refining channel mapping happens via a follow-up
endpoint instead of a second upload.

_render_cv_plot_png/_render_sr_plot_png are new matplotlib builders (not
imports from modes/cyclic_voltammetry.py, whose real closures aren't
importable) — same reasoning and same code shape as the ones already
proven out in macos_app/ui/modes/cyclic_voltammetry_view.py.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.constants import PAL
from core.numeric import lin_reg, to_num
from core.parsing import parse_potentiostat_csv
from core.plotting import _ORIGIN_RC, _MINIMAL_RC, _apply_spine_style
from modes.cyclic_voltammetry import find_cv_peaks
from web_app.api.common import figure_json, png_response
from web_app.deps import get_session
from web_app.session import SessionData

router = APIRouter(prefix="/api/cv", tags=["cyclic_voltammetry"])

_DELIM_MAP = {"auto": None, "comma": ",", "tab": "\t", "semicolon": ";", "space": r"\s+"}


def _dedup_cols(cols: list[str]) -> list[str]:
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


def _decode_text(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def _parse_cv_csv(raw: str, fmt: str, delimiter: str, skip_rows: int) -> tuple[pd.DataFrame, list[dict]]:
    d = _DELIM_MAP[delimiter]
    if d is None:
        lines = raw.splitlines()
        sniff = lines[skip_rows] if skip_rows < len(lines) else (lines[0] if lines else "")
        d = next((c for c in [",", "\t", ";"] if c in sniff), r"\s+")
    if fmt == "multichannel":
        df, auto_channels = parse_potentiostat_csv(raw, d, mode="cv")
        df.columns = _dedup_cols(list(df.columns))
        auto_channels = [ch for ch in auto_channels if ch.get("vc") in df.columns and ch.get("ic") in df.columns]
        return df, auto_channels
    engine = "python" if d == r"\s+" else "c"
    df = pd.read_csv(io.StringIO(raw), sep=d, skiprows=skip_rows, engine=engine, skipinitialspace=True)
    df.columns = _dedup_cols([c.lstrip("﻿").strip() for c in df.columns])
    return df, []


def _fallback_cv_channels(columns: list[str]) -> list[dict]:
    n = max(1, len(columns) // 2)
    return [{"name": f"CH{i + 1}", "vc": columns[2 * i], "ic_cols": [columns[2 * i + 1]]}
            for i in range(n) if 2 * i + 1 < len(columns)] or (
        [{"name": "CH1", "vc": columns[0], "ic_cols": [columns[0]]}] if columns else []
    )


def _guess_scan_rate(filename: str) -> float:
    nums = re.findall(r"\d+\.?\d*", filename.rsplit(".", 1)[0])
    return max(float(nums[-1]), 0.001) if nums else 10.0


# -- state ------------------------------------------------------------------------
def _state(session: SessionData) -> dict:
    runs = session.cv_runs
    return {
        "runs": [
            {
                "filename": r["filename"], "scan_rate": r["scan_rate"], "label": r["label"],
                "n_rows": len(r["df"]), "channels": r["channels"],
                "n_peaks": sum(len(v.get("anodic", [])) + len(v.get("cathodic", [])) for v in r["peaks"].values()),
            }
            for r in runs
        ],
        "volt_unit": session.volt_unit,
        "cur_unit": session.cv_cur_unit,
        "sr_unit": session.cv_sr_unit,
        "channel_names": list(dict.fromkeys(c["name"] for r in runs for c in r["channels"])),
        "scan_rate_labels": [r["label"] for r in runs],
    }


@router.get("/state")
def get_state(session: SessionData = Depends(get_session)) -> dict:
    return _state(session)


class UnitsBody(BaseModel):
    volt_unit: str | None = None
    cur_unit: str | None = None
    sr_unit: str | None = None


@router.post("/units")
def set_units(body: UnitsBody, session: SessionData = Depends(get_session)) -> dict:
    if body.volt_unit is not None:
        session.volt_unit = body.volt_unit
    if body.cur_unit is not None:
        session.cv_cur_unit = body.cur_unit
    if body.sr_unit is not None:
        session.cv_sr_unit = body.sr_unit
    return _state(session)


# -- Import ---------------------------------------------------------------------
@router.post("/files")
async def upload_files(
    files: list[UploadFile] = File(...),
    fmt: str = Form("standard"),
    delimiter: str = Form("auto"),
    skip_rows: int = Form(0),
    session: SessionData = Depends(get_session),
) -> dict:
    existing_peaks = {(r["filename"], r["scan_rate"]): r["peaks"] for r in session.cv_runs}
    new_runs = []
    errors = []
    for upload in files:
        filename = upload.filename or "upload.csv"
        try:
            raw_bytes = await upload.read()
            raw = _decode_text(raw_bytes)
            df, auto_channels = _parse_cv_csv(raw, fmt, delimiter, skip_rows)
            channels_spec = auto_channels or _fallback_cv_channels(list(df.columns))
            channels = []
            for spec in channels_spec:
                ic_cols = spec.get("ic_cols") or ([spec["ic"]] if "ic" in spec else [])
                if not ic_cols:
                    continue
                if len(ic_cols) == 1:
                    ic_col = ic_cols[0]
                    is_avg = False
                else:
                    ic_arrs = [to_num(df[c]).to_numpy(dtype=float, na_value=np.nan) for c in ic_cols if c in df.columns]
                    max_len = max((len(a) for a in ic_arrs), default=0)
                    mat = np.full((len(ic_arrs), max_len), np.nan)
                    for j, arr in enumerate(ic_arrs):
                        mat[j, :len(arr)] = arr
                    ic_col = f"__avg_{spec['name']}_ic"
                    df[ic_col] = np.nanmean(mat, axis=0)
                    is_avg = True
                channels.append({"name": spec["name"], "vc": spec["vc"], "ic": ic_col, "is_avg": is_avg})
            sr_val = _guess_scan_rate(filename)
            new_runs.append({
                "scan_rate": sr_val, "label": f"{sr_val:g} {session.cv_sr_unit}", "filename": filename,
                "df": df, "channels": channels, "peaks": existing_peaks.get((filename, sr_val), {}),
            })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{filename}: {exc}")
    all_runs = session.cv_runs + new_runs
    all_runs.sort(key=lambda r: r["scan_rate"])
    session.cv_runs = all_runs
    result = _state(session)
    if errors:
        result["errors"] = errors
    return result


class ScanRateBody(BaseModel):
    scan_rate: float


@router.post("/runs/{index}/scan-rate")
def set_scan_rate(index: int, body: ScanRateBody, session: SessionData = Depends(get_session)) -> dict:
    if not (0 <= index < len(session.cv_runs)):
        raise HTTPException(status_code=404, detail=f"No run at index {index}")
    run = session.cv_runs[index]
    session.cv_runs[index] = {**run, "scan_rate": body.scan_rate, "label": f"{body.scan_rate:g} {session.cv_sr_unit}"}
    return _state(session)


class ChannelsBody(BaseModel):
    channels: list[dict]  # [{"name","vc","ic_cols":[...]}]


@router.post("/runs/{index}/channels")
def set_channels(index: int, body: ChannelsBody, session: SessionData = Depends(get_session)) -> dict:
    if not (0 <= index < len(session.cv_runs)):
        raise HTTPException(status_code=404, detail=f"No run at index {index}")
    run = session.cv_runs[index]
    df = run["df"]
    channels = []
    for spec in body.channels:
        ic_cols = spec.get("ic_cols") or []
        if not ic_cols:
            continue
        if len(ic_cols) == 1:
            ic_col, is_avg = ic_cols[0], False
        else:
            ic_arrs = [to_num(df[c]).to_numpy(dtype=float, na_value=np.nan) for c in ic_cols if c in df.columns]
            max_len = max((len(a) for a in ic_arrs), default=0)
            mat = np.full((len(ic_arrs), max_len), np.nan)
            for j, arr in enumerate(ic_arrs):
                mat[j, :len(arr)] = arr
            ic_col = f"__avg_{spec['name']}_ic"
            df[ic_col] = np.nanmean(mat, axis=0)
            is_avg = True
        channels.append({"name": spec["name"], "vc": spec["vc"], "ic": ic_col, "is_avg": is_avg})
    session.cv_runs[index] = {**run, "df": df, "channels": channels}
    return _state(session)


@router.get("/runs/{index}/columns")
def get_columns(index: int, session: SessionData = Depends(get_session)) -> dict:
    if not (0 <= index < len(session.cv_runs)):
        raise HTTPException(status_code=404, detail=f"No run at index {index}")
    return {"columns": list(session.cv_runs[index]["df"].columns), "channels": session.cv_runs[index]["channels"]}


# -- CV plot ----------------------------------------------------------------------
class PlotBody(BaseModel):
    visible_srs: list[str]
    visible_chs: list[str]


def _build_cv_figure(runs: list[dict], vis_srs: set[str], vis_chs: set[str], volt_unit: str, cur_unit: str) -> go.Figure:
    vis_runs = [r for r in runs if r["label"] in vis_srs]
    n_vis = len(vis_runs)
    fig = go.Figure()
    for rank, run in enumerate(vis_runs):
        opacity = 0.30 + 0.70 * (rank / max(1, n_vis - 1))
        for ci, ch in enumerate(run["channels"]):
            if ch["name"] not in vis_chs:
                continue
            col = PAL[ci % len(PAL)]
            v = to_num(run["df"][ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
            i = to_num(run["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            fig.add_trace(go.Scatter(x=v, y=i, name=run["label"], legendgroup=ch["name"],
                                      legendgrouptitle=dict(text=ch["name"]), mode="lines",
                                      opacity=opacity, line=dict(color=col, width=1.8)))
            for pt_key, sym in [("anodic", "triangle-up"), ("cathodic", "triangle-down")]:
                for p in run["peaks"].get(ch["name"], {}).get(pt_key, []):
                    fig.add_trace(go.Scatter(x=[p["Ep"]], y=[p["Ip"]], mode="markers", showlegend=False,
                                              legendgroup=ch["name"], opacity=opacity,
                                              marker=dict(symbol=sym, size=10, color=col)))
    fig.add_hline(y=0, line=dict(color="rgba(0,0,0,0.25)", width=1, dash="dash"))
    fig.update_layout(
        xaxis_title=f"Potential ({volt_unit})", yaxis_title=f"Current ({cur_unit})",
        height=520, template="plotly_white",
        legend=dict(orientation="v", x=1.02, y=1, xanchor="left", groupclick="toggleitem"),
    )
    return fig


@router.post("/plot")
def get_plot(body: PlotBody, session: SessionData = Depends(get_session)) -> dict:
    fig = _build_cv_figure(session.cv_runs, set(body.visible_srs), set(body.visible_chs), session.volt_unit, session.cv_cur_unit)
    return figure_json(fig)


# -- Peak detection -----------------------------------------------------------------
class PeaksBody(BaseModel):
    channels: list[str]
    prominence: float = 0.1
    distance: int = 10
    width: int | None = None
    height: float | None = None
    visible_srs: list[str] = []
    visible_chs: list[str] = []


@router.post("/peaks/detect")
def detect_peaks(body: PeaksBody, session: SessionData = Depends(get_session)) -> dict:
    if not body.channels:
        raise HTTPException(status_code=400, detail="Select at least one channel.")
    new_runs = []
    for run in session.cv_runs:
        new_run = dict(run)
        peaks = {}
        for ch in run["channels"]:
            if ch["name"] not in body.channels:
                continue
            v = to_num(run["df"][ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
            i = to_num(run["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
            peaks[ch["name"]] = find_cv_peaks(v, i, body.prominence, body.distance, body.width, body.height)
        new_run["peaks"] = peaks
        new_runs.append(new_run)
    session.cv_runs = new_runs

    rows = []
    for r in session.cv_runs:
        for ch_name, pk in r["peaks"].items():
            for p in pk.get("anodic", []):
                rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Anodic", "Ep": p["Ep"], "Ip": p["Ip"]})
            for p in pk.get("cathodic", []):
                rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Cathodic", "Ep": p["Ep"], "Ip": p["Ip"]})

    vis_srs = set(body.visible_srs) or {r["label"] for r in session.cv_runs}
    vis_chs = set(body.visible_chs) or {c["name"] for r in session.cv_runs for c in r["channels"]}
    fig = _build_cv_figure(session.cv_runs, vis_srs, vis_chs, session.volt_unit, session.cv_cur_unit)
    return {"figure": figure_json(fig), "peaks": rows, "state": _state(session)}


# -- Scan rate analysis --------------------------------------------------------------
def _main_peak(lst):
    return max(lst, key=lambda p: abs(p["Ip"])) if lst else None


def _scan_rate_data(runs: list[dict], channels: list[str]) -> dict[str, pd.DataFrame]:
    ch_data = {}
    for ch_name in channels:
        rows = []
        for run in runs:
            pk = run["peaks"].get(ch_name, {})
            pa, pc = _main_peak(pk.get("anodic", [])), _main_peak(pk.get("cathodic", []))
            epa = pa["Ep"] if pa else np.nan
            epc = pc["Ep"] if pc else np.nan
            both_finite = np.isfinite(epa) and np.isfinite(epc)
            rows.append({
                "scan_rate": run["scan_rate"], "label": run["label"],
                "Ip_a": pa["Ip"] if pa else np.nan, "Ep_a": epa,
                "Ip_c": pc["Ip"] if pc else np.nan, "Ep_c": epc,
                "delta_Ep": abs(epa - epc) if both_finite else np.nan,
                "E_half": (epa + epc) / 2 if both_finite else np.nan,
            })
        ch_data[ch_name] = pd.DataFrame(rows).sort_values("scan_rate").reset_index(drop=True)
    return ch_data


class ScanRateReqBody(BaseModel):
    channels: list[str]


@router.post("/scan-rate")
def scan_rate_analysis(body: ScanRateReqBody, session: SessionData = Depends(get_session)) -> dict:
    ch_data = _scan_rate_data(session.cv_runs, body.channels)
    if not ch_data:
        return {"figures": {}, "stats": []}
    cur_unit, sr_unit, volt_unit = session.cv_cur_unit, session.cv_sr_unit, session.volt_unit
    common = dict(template="plotly_white", height=340)

    fig_ip_nu, fig_ip_sqrt = go.Figure(), go.Figure()
    stat_rows = []
    for ci, (ch_name, d) in enumerate(ch_data.items()):
        col = PAL[ci % len(PAL)]
        nu = d["scan_rate"].values
        snu = np.sqrt(nu)
        for pt, ipcol, sym, dash in [("anodic", "Ip_a", "triangle-up", "solid"), ("cathodic", "Ip_c", "triangle-down", "dash")]:
            ip = d[ipcol].values
            valid = np.isfinite(ip)
            if not valid.any():
                continue
            lbl = f"{ch_name} ({pt})"
            fig_ip_nu.add_trace(go.Scatter(x=nu[valid], y=ip[valid], name=lbl, mode="markers+lines",
                                            marker=dict(symbol=sym, size=9, color=col), line=dict(color=col, dash=dash)))
            fig_ip_sqrt.add_trace(go.Scatter(x=snu[valid], y=ip[valid], name=lbl, mode="markers",
                                              marker=dict(symbol=sym, size=9, color=col)))
            fit = lin_reg(snu[valid], ip[valid])
            if fit:
                xf = np.linspace(snu[valid].min(), snu[valid].max(), 200)
                fig_ip_sqrt.add_trace(go.Scatter(x=xf, y=fit["slope"] * xf + fit["intercept"],
                                                  name=f"{lbl} fit (R²={fit['r2']:.3f})", mode="lines",
                                                  line=dict(color=col, dash="dot", width=2)))
                stat_rows.append({"Channel": ch_name, "Peak": pt, "Slope": f"{fit['slope']:.4g}",
                                   "R²": f"{fit['r2']:.4f}", "N runs": int(valid.sum())})
    fig_ip_nu.update_layout(**common, xaxis_title=f"Scan rate ν ({sr_unit})", yaxis_title=f"Ip ({cur_unit})")
    fig_ip_sqrt.update_layout(**common, xaxis_title="√ν", yaxis_title=f"Ip ({cur_unit})")

    fig_ep_nu = go.Figure()
    for ci, (ch_name, d) in enumerate(ch_data.items()):
        col = PAL[ci % len(PAL)]
        nu = d["scan_rate"].values
        for pt, epcol, sym, dash in [("anodic", "Ep_a", "triangle-up", "solid"),
                                      ("cathodic", "Ep_c", "triangle-down", "dash"),
                                      ("E½", "E_half", "circle", "dot")]:
            ep = d[epcol].values
            valid = np.isfinite(ep)
            if not valid.any():
                continue
            fig_ep_nu.add_trace(go.Scatter(x=nu[valid], y=ep[valid], name=f"{ch_name} {pt}", mode="markers+lines",
                                            marker=dict(symbol=sym, size=8, color=col), line=dict(color=col, dash=dash)))
    fig_ep_nu.update_layout(**common, xaxis_title=f"Scan rate ν ({sr_unit})", yaxis_title=f"Potential ({volt_unit})")

    fig_dep_nu = go.Figure()
    for ci, (ch_name, d) in enumerate(ch_data.items()):
        col = PAL[ci % len(PAL)]
        nu = d["scan_rate"].values
        dep = d["delta_Ep"].values
        valid = np.isfinite(dep)
        if not valid.any():
            continue
        fig_dep_nu.add_trace(go.Scatter(x=nu[valid], y=dep[valid], name=ch_name, mode="markers+lines",
                                         marker=dict(symbol="circle", size=9, color=col), line=dict(color=col)))
    fig_dep_nu.update_layout(**common, xaxis_title=f"Scan rate ν ({sr_unit})", yaxis_title=f"ΔEp ({volt_unit})")

    return {
        "figures": {
            "ip_nu": figure_json(fig_ip_nu), "ip_sqrt_nu": figure_json(fig_ip_sqrt),
            "ep_nu": figure_json(fig_ep_nu), "delta_ep": figure_json(fig_dep_nu),
        },
        "stats": stat_rows,
    }


# -- Export -----------------------------------------------------------------------
def _render_cv_plot_png(runs, visible_srs, visible_chs, volt_unit, cur_unit, dpi=150, fmt="png", figsize=None, style="default") -> bytes:
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")
    _rc = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    vis_runs = [r for r in runs if r["label"] in visible_srs]
    n = len(vis_runs)
    with matplotlib.rc_context(_rc):
        cmap = matplotlib.colormaps["viridis"]
        fig, ax = plt.subplots(figsize=figsize or (9, 6))
        for ri, run in enumerate(vis_runs):
            color = cmap(ri / max(1, n - 1))
            for ci, ch in enumerate(run["channels"]):
                if ch["name"] not in visible_chs:
                    continue
                v = to_num(run["df"][ch["vc"]]).to_numpy(dtype=float, na_value=np.nan)
                i = to_num(run["df"][ch["ic"]]).to_numpy(dtype=float, na_value=np.nan)
                ax.plot(v, i, color=color, linestyle=["-", "--", ":", "-."][ci % 4],
                        linewidth=1.4, label=run["label"] if ci == 0 else None)
                for p in run["peaks"].get(ch["name"], {}).get("anodic", []):
                    ax.plot(p["Ep"], p["Ip"], "^", color=color, markersize=8, zorder=5)
                for p in run["peaks"].get(ch["name"], {}).get("cathodic", []):
                    ax.plot(p["Ep"], p["Ip"], "v", color=color, markersize=8, zorder=5)
        ax.axhline(0, color="#bbbbbb", linewidth=0.8, linestyle="--")
        ax.set_xlabel(f"Potential ({volt_unit})")
        ax.set_ylabel(f"Current ({cur_unit})")
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _render_sr_plot_png(kind, ch_data, volt_unit, cur_unit, sr_unit, dpi=150, fmt="png", figsize=None, style="default") -> bytes:
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")
    _rc = {"origin": _ORIGIN_RC, "minimal": _MINIMAL_RC}.get(style, {})
    with matplotlib.rc_context(_rc):
        fig, ax = plt.subplots(figsize=figsize or (7, 5))
        for ci, (ch_name, d) in enumerate(ch_data.items()):
            col = PAL[ci % len(PAL)]
            nu = d["scan_rate"].to_numpy(dtype=float)
            x = np.sqrt(nu) if kind == "ip_sqrt_nu" else nu
            if kind == "delta_ep":
                y = d["delta_Ep"].to_numpy(dtype=float)
                ok = np.isfinite(y)
                if ok.any():
                    ax.plot(x[ok], y[ok], color=col, linestyle="-", marker="o", markersize=6, linewidth=1.4, label=ch_name)
                continue
            series = [("anodic", "Ip_a", "^", "-"), ("cathodic", "Ip_c", "v", "--")]
            if kind == "ep_nu":
                series = [("anodic", "Ep_a", "^", "-"), ("cathodic", "Ep_c", "v", "--"), ("E½", "E_half", "o", ":")]
            for pt, ycol, marker, ls in series:
                y = d[ycol].to_numpy(dtype=float)
                ok = np.isfinite(y)
                if not ok.any():
                    continue
                ax.plot(x[ok], y[ok], color=col, linestyle=ls, marker=marker, markersize=6, linewidth=1.4, label=f"{ch_name} ({pt})")
                if kind == "ip_sqrt_nu":
                    fit = lin_reg(x[ok], y[ok])
                    if fit:
                        xf = np.linspace(x[ok].min(), x[ok].max(), 200)
                        ax.plot(xf, fit["slope"] * xf + fit["intercept"], color=col, linestyle=":", linewidth=1.2)
        x_labels = {
            "ip_nu": f"Scan rate ν ({sr_unit})", "ip_sqrt_nu": f"√ Scan rate  √ν  (√{sr_unit})",
            "ep_nu": f"Scan rate ν ({sr_unit})", "delta_ep": f"Scan rate ν ({sr_unit})",
        }
        y_labels = {
            "ip_nu": f"Peak current Ip ({cur_unit})", "ip_sqrt_nu": f"Peak current Ip ({cur_unit})",
            "ep_nu": f"Potential ({volt_unit})", "delta_ep": f"ΔEp ({volt_unit})",
        }
        ax.set_xlabel(x_labels[kind])
        ax.set_ylabel(y_labels[kind])
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
        _apply_spine_style(ax, style)
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


class ExportPlotBody(BaseModel):
    visible_srs: list[str]
    visible_chs: list[str]
    fmt: str = "png"
    dpi: int = 150
    style: str = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/plot")
def export_plot(body: ExportPlotBody, session: SessionData = Depends(get_session)):
    if not session.cv_runs:
        raise HTTPException(status_code=400, detail="No runs loaded.")
    png_bytes = _render_cv_plot_png(
        session.cv_runs, set(body.visible_srs), set(body.visible_chs), session.volt_unit, session.cv_cur_unit,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
    )
    media_type = {"svg": "image/svg+xml", "pdf": "application/pdf", "tiff": "image/tiff"}.get(body.fmt, "image/png")
    return png_response(png_bytes, f"cv_plot.{body.fmt}", media_type)


class ExportScanRatePlotBody(BaseModel):
    kind: str
    channels: list[str]
    fmt: str = "png"
    dpi: int = 150
    style: str = "default"
    figsize: tuple[float, float] | None = None


@router.post("/export/scan-rate-plot")
def export_scan_rate_plot(body: ExportScanRatePlotBody, session: SessionData = Depends(get_session)):
    ch_data = _scan_rate_data(session.cv_runs, body.channels)
    if not ch_data:
        raise HTTPException(status_code=400, detail="No peak data for the selected channels.")
    png_bytes = _render_sr_plot_png(
        body.kind, ch_data, session.volt_unit, session.cv_cur_unit, session.cv_sr_unit,
        dpi=body.dpi, fmt=body.fmt, figsize=body.figsize, style=body.style,
    )
    media_type = {"svg": "image/svg+xml", "pdf": "application/pdf", "tiff": "image/tiff"}.get(body.fmt, "image/png")
    return png_response(png_bytes, f"cv_{body.kind}.{body.fmt}", media_type)


@router.get("/export/peaks-csv")
def export_peaks_csv(session: SessionData = Depends(get_session)):
    rows = []
    for r in session.cv_runs:
        for ch_name, pk in r["peaks"].items():
            for p in pk.get("anodic", []):
                rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Anodic", "Ep": p["Ep"], "Ip": p["Ip"]})
            for p in pk.get("cathodic", []):
                rows.append({"Scan rate": r["scan_rate"], "Channel": ch_name, "Type": "Cathodic", "Ep": p["Ep"], "Ip": p["Ip"]})
    if not rows:
        raise HTTPException(status_code=400, detail="No peaks detected yet.")
    csv_text = pd.DataFrame(rows).to_csv(index=False)
    return png_response(csv_text.encode("utf-8"), "cv_peaks_all.csv", "text/csv")


class ScanRateCsvBody(BaseModel):
    channels: list[str]


@router.post("/export/scan-rate-csv")
def export_scan_rate_csv(body: ScanRateCsvBody, session: SessionData = Depends(get_session)):
    ch_data = _scan_rate_data(session.cv_runs, body.channels)
    rows = []
    for ch_name, d in ch_data.items():
        for _, row in d.iterrows():
            rows.append({"Channel": ch_name, "Scan rate": row["scan_rate"], "Ip_a": row["Ip_a"],
                         "Ep_a": row["Ep_a"], "Ip_c": row["Ip_c"], "Ep_c": row["Ep_c"],
                         "delta_Ep": row["delta_Ep"], "E_half": row["E_half"]})
    if not rows:
        raise HTTPException(status_code=400, detail="No scan-rate data for the selected channels.")
    csv_text = pd.DataFrame(rows).to_csv(index=False)
    return png_response(csv_text.encode("utf-8"), "cv_scan_rate_analysis.csv", "text/csv")


@router.get("/export/raw")
def export_raw(session: SessionData = Depends(get_session)):
    if not session.cv_runs:
        raise HTTPException(status_code=400, detail="No runs loaded.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in session.cv_runs:
            safe = r["label"].replace("/", "per").replace(" ", "_")
            zf.writestr(f"cv_raw_{safe}.csv", r["df"].to_csv(index=False))
    buf.seek(0)
    return png_response(buf.getvalue(), "cv_raw_data.zip", "application/zip")
