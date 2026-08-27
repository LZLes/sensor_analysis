import numpy as np

from modes.cyclic_voltammetry import find_cv_peaks


def _triangle_cv_trace():
    v = np.concatenate([np.linspace(-0.5, 0.5, 200), np.linspace(0.5, -0.5, 200)])
    anodic_peak = 5.0 * np.exp(-((v - 0.1) ** 2) / 0.0005)
    cathodic_peak = -4.0 * np.exp(-((v + 0.1) ** 2) / 0.0005)
    i = anodic_peak + cathodic_peak
    return v, i


def test_find_cv_peaks_detects_anodic_and_cathodic():
    v, i = _triangle_cv_trace()
    out = find_cv_peaks(v, i, prominence=1.0, distance=5)
    assert len(out["anodic"]) >= 1
    assert len(out["cathodic"]) >= 1
    top_anodic = max(out["anodic"], key=lambda p: p["Ip"])
    assert abs(top_anodic["Ep"] - 0.1) < 0.05
    top_cathodic = min(out["cathodic"], key=lambda p: p["Ip"])
    assert abs(top_cathodic["Ep"] - (-0.1)) < 0.05


def test_find_cv_peaks_prominence_zero_means_no_filter():
    v, i = _triangle_cv_trace()
    strict = find_cv_peaks(v, i, prominence=100.0, distance=5)
    lenient = find_cv_peaks(v, i, prominence=0.0, distance=5)
    # 0 prominence must not filter out everything the way a very high
    # prominence does (regression for prominence=0 not behaving as "no filter").
    assert len(lenient["anodic"]) >= len(strict["anodic"])
    assert len(lenient["anodic"]) > 0


def test_find_cv_peaks_short_trace_returns_empty():
    v = np.array([0.0, 0.1, 0.2])
    i = np.array([1.0, 2.0, 1.0])
    out = find_cv_peaks(v, i, prominence=0.1, distance=1)
    assert out == {"anodic": [], "cathodic": []}


def test_find_cv_peaks_ignores_nan():
    v, i = _triangle_cv_trace()
    v = v.copy()
    i = i.copy()
    v[10] = np.nan
    i[20] = np.nan
    out = find_cv_peaks(v, i, prominence=1.0, distance=5)
    assert len(out["anodic"]) >= 1


def test_find_cv_peaks_height_filter_excludes_small_peaks():
    v, i = _triangle_cv_trace()
    out_no_height = find_cv_peaks(v, i, prominence=0.5, distance=5, height=None)
    out_high_height = find_cv_peaks(v, i, prominence=0.5, distance=5, height=100.0)
    assert len(out_high_height["anodic"]) == 0
    assert len(out_no_height["anodic"]) >= 1
