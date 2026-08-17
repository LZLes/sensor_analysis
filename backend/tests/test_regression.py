import numpy as np
import pytest

from app.analysis.regression import lin_reg, piecewise_fit


def test_lin_reg_perfect_line():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = 2.0 * x + 1.0
    fit = lin_reg(x, y)
    assert fit is not None
    assert fit["slope"] == pytest.approx(2.0)
    assert fit["intercept"] == pytest.approx(1.0)
    assert fit["r2"] == pytest.approx(1.0)


def test_lin_reg_insufficient_points():
    assert lin_reg(np.array([1.0]), np.array([1.0])) is None


def test_lin_reg_degenerate_x():
    assert lin_reg(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0])) is None


def test_lin_reg_nan_handling():
    x = np.array([0.0, 1.0, np.nan, 3.0])
    y = np.array([1.0, 3.0, 99.0, 7.0])
    fit = lin_reg(x, y)
    assert fit is not None
    # y = 2x + 1 on the 3 remaining valid points
    assert fit["slope"] == pytest.approx(2.0)


def test_piecewise_fit_single_segment_is_linear():
    x = np.linspace(0, 10, 20)
    y = 3.0 * x + 2.0
    result = piecewise_fit(x, y, 1)
    assert len(result["segments"]) == 1
    assert result["breakpoints"] == []
    assert result["segments"][0]["slope"] == pytest.approx(3.0)


def test_piecewise_fit_two_segments_finds_breakpoint():
    # Two clearly different slopes meeting at x=5
    x = np.arange(0, 11, dtype=float)
    y = np.where(x <= 5, x, 5 + 3 * (x - 5))
    result = piecewise_fit(x, y, 2)
    assert len(result["segments"]) == 2
    assert len(result["breakpoints"]) == 1
    # breakpoint should land near x=5
    assert abs(result["breakpoints"][0] - 5.0) <= 1.0
    seg1, seg2 = result["segments"]
    assert seg1["slope"] == pytest.approx(1.0, abs=0.2)
    assert seg2["slope"] == pytest.approx(3.0, abs=0.3)
    # continuity: segments must meet exactly at the breakpoint
    bp = result["breakpoints"][0]
    y1_at_bp = seg1["slope"] * bp + seg1["intercept"]
    y2_at_bp = seg2["slope"] * bp + seg2["intercept"]
    assert y1_at_bp == pytest.approx(y2_at_bp, abs=1e-6)


def test_piecewise_fit_too_few_points_falls_back_to_single():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([0.0, 1.0, 2.0, 3.0])
    result = piecewise_fit(x, y, 3)   # n=4 < n_seg*2=6 -> falls back
    assert len(result["segments"]) == 1
    assert result["breakpoints"] == []


def test_piecewise_fit_empty_input():
    result = piecewise_fit(np.array([]), np.array([]), 2)
    assert result == {"segments": [], "breakpoints": []}
