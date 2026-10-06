"""Solid-State (potentiometric) maths: Nernstian slope and LOD fit, calibration-
table presets, sample data and the calibration-curve export figure. Used by
modes/solid_state.py (Streamlit) and web_app/api/solid_state.py."""

import io
import os

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from core.constants import _SAMPLE_DATA_DIR, PAL
from core.numeric import lin_reg
from core.plotting import _MINIMAL_RC, _ORIGIN_RC, _apply_spine_style


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


# Recognized voltage-unit spellings, in mV per 1 configured unit — lets the
# "% of ideal" comparison stay correct even when solid_unit (free text) is
# something other than mV, instead of silently comparing mismatched units.
_MV_PER_SIGNAL_UNIT = {
    "mv": 1.0, "millivolt": 1.0, "millivolts": 1.0,
    "v": 1000.0, "volt": 1000.0, "volts": 1000.0,
    "uv": 0.001, "µv": 0.001, "microvolt": 0.001, "microvolts": 0.001,
}


def ideal_slope_in_signal_unit(temp_c: float, z: int, signal_unit: str) -> float | None:
    """nernst_ideal_slope_mv() converted into signal_unit, so it can be
    compared directly against a slope fitted from data in that unit. None
    if signal_unit isn't a recognized voltage unit — callers should show
    "unit not recognized" rather than silently comparing mismatched units."""
    factor = _MV_PER_SIGNAL_UNIT.get(str(signal_unit).strip().lower())
    if factor is None:
        return None
    return nernst_ideal_slope_mv(temp_c, z) / factor


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
