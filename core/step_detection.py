"""Detect concentration-step transition times directly from a trace, as an
alternative to typing t_start/t_end off the Time Series chart by eye or
generating them from an assumed even-spacing preset (_preset_cpdf_amp /
_preset_cpdf_solid). Two runs can have concentration added at slightly
different times — this reads the real times off each run's own trace.

Pure/mode-agnostic: no Streamlit, no cpdf schema assumptions. Shared by
both Amperometry and Solid-State via core.shared_tabs._render_autodetect_expander.
"""

import numpy as np
from scipy.signal import find_peaks


def detect_step_edges(
    t: np.ndarray,
    signal: np.ndarray,
    *,
    min_step_seconds: float = 30.0,
    sensitivity: float = 1.0,
    max_edges: int | None = None,
) -> list[float]:
    """Returns sorted candidate transition times where the trace jumps to a
    new level (a concentration step being added). Empty list if none found
    (flat/no-signal trace, or nothing clears the sensitivity threshold).

    Algorithm: derivative of the (optionally pre-smoothed by the caller)
    signal w.r.t. time, peak-find on |derivative| — same
    scipy.signal.find_peaks primitive find_cv_peaks already uses for CV
    peaks, just applied to the rate of change of a time trace instead of
    the trace itself.
    """
    t = np.asarray(t, dtype=float)
    signal = np.asarray(signal, dtype=float)
    mask = ~(np.isnan(t) | np.isnan(signal))
    if mask.sum() < 5:
        return []
    order = np.argsort(t[mask])
    t = t[mask][order]
    signal = signal[mask][order]

    deriv = np.gradient(signal, t)
    med = np.median(deriv)
    # Robust noise floor (MAD) — one default works across µA-scale
    # amperometry signals and mV-scale solid-state signals without
    # per-unit tuning.
    mad = np.median(np.abs(deriv - med))
    sigma = 1.4826 * mad
    floor = 1e-9 * (np.ptp(signal) if np.ptp(signal) > 0 else 1.0)
    prominence = max(sensitivity * 6.0 * sigma, floor)

    dt = np.median(np.diff(t))
    distance = max(1, round(min_step_seconds / dt)) if dt > 0 else 1

    peaks, props = find_peaks(np.abs(deriv), prominence=prominence, distance=distance)
    if peaks.size == 0:
        return []

    if max_edges is not None and peaks.size > max_edges:
        keep = np.argsort(props["prominences"])[::-1][:max_edges]
        peaks = np.sort(peaks[keep])

    return [float(t[i]) for i in peaks]


def edges_to_windows(
    edges: list[float],
    trace_end: float,
    include_leading_baseline: bool = False,
) -> list[tuple[str, float, float]]:
    """Turns detected edges into (label, t_start, t_end) triples: each edge
    marks the START of a new window (the moment a step was added), and its
    END is the next edge (or trace_end for the last one). If
    include_leading_baseline, prepends a "Baseline" row from t=0 to the
    first edge."""
    if not edges:
        return []
    edges = sorted(edges)
    windows = []
    if include_leading_baseline:
        windows.append(("Baseline", 0.0, edges[0]))
    for i, edge in enumerate(edges):
        t_end = edges[i + 1] if i + 1 < len(edges) else trace_end
        windows.append((f"Step {i + 1}", edge, t_end))
    return windows
