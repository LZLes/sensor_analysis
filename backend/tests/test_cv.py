import numpy as np
import pytest

from app.analysis.cv import find_cv_peaks


def test_find_cv_peaks_detects_anodic_and_cathodic():
    v = np.linspace(-1, 1, 400)
    # one clear anodic (positive) peak and one clear cathodic (negative) peak
    current = 5.0 * np.exp(-((v - 0.3) ** 2) / 0.01) - 4.0 * np.exp(-((v + 0.2) ** 2) / 0.01)
    peaks = find_cv_peaks(v, current, prominence=1.0, distance=5)
    assert len(peaks["anodic"]) >= 1
    assert len(peaks["cathodic"]) >= 1
    assert peaks["anodic"][0]["Ep"] == pytest.approx(0.3, abs=0.05)


def test_find_cv_peaks_too_few_points_returns_empty():
    v = np.array([0.0, 0.1, 0.2])
    i = np.array([1.0, 2.0, 1.0])
    peaks = find_cv_peaks(v, i, prominence=0.1, distance=1)
    assert peaks == {"anodic": [], "cathodic": []}


def test_find_cv_peaks_nan_filtered():
    v = np.linspace(-1, 1, 400)
    current = 5.0 * np.exp(-((v - 0.3) ** 2) / 0.01)
    v_with_nan = v.copy()
    current_with_nan = current.copy()
    v_with_nan[10] = np.nan
    peaks = find_cv_peaks(v_with_nan, current_with_nan, prominence=1.0, distance=5)
    assert len(peaks["anodic"]) >= 1
