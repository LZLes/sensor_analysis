import numpy as np
import pandas as pd
import pytest

from core.numeric import to_num, smooth_signal, lin_reg, _eff_t_start, _is_float


def test_to_num_coerces_and_keeps_nan():
    s = pd.Series(["1.5", "bad", "3"])
    out = to_num(s)
    assert out.tolist()[0] == 1.5
    assert np.isnan(out.tolist()[1])
    assert out.tolist()[2] == 3.0


def test_smooth_signal_none_is_noop():
    arr = np.array([1.0, 2.0, 3.0])
    assert smooth_signal(arr, "None", 5) is arr


def test_smooth_signal_empty_array():
    arr = np.array([])
    out = smooth_signal(arr, "Moving average", 5)
    assert out.size == 0


def test_smooth_signal_moving_average_smooths_noise():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 200)
    clean = np.sin(x)
    noisy = clean + 0.3 * rng.standard_normal(x.size)
    smoothed = smooth_signal(noisy, "Moving average", 11)
    assert np.std(smoothed - clean) < np.std(noisy - clean)


def test_smooth_signal_savgol_window_clamped_to_array_size():
    arr = np.array([1.0, 2.0, 1.0, 2.0, 1.0])
    out = smooth_signal(arr, "Savitzky-Golay", 999, polyorder=2)
    assert out.size == arr.size
    assert np.all(np.isfinite(out))


def test_lin_reg_perfect_line():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = 2.0 * x + 1.0
    fit = lin_reg(x, y)
    assert fit is not None
    assert fit["slope"] == pytest.approx(2.0)
    assert fit["intercept"] == pytest.approx(1.0)
    assert fit["r2"] == pytest.approx(1.0)


def test_lin_reg_too_few_points_returns_none():
    assert lin_reg(np.array([1.0]), np.array([1.0])) is None
    assert lin_reg(np.array([]), np.array([])) is None


def test_lin_reg_constant_x_returns_none():
    x = np.array([2.0, 2.0, 2.0])
    y = np.array([1.0, 2.0, 3.0])
    assert lin_reg(x, y) is None


def test_lin_reg_ignores_nan_pairs():
    x = np.array([0.0, 1.0, np.nan, 3.0])
    y = np.array([0.0, 1.0, 5.0, 3.0])
    fit = lin_reg(x, y)
    assert fit is not None
    assert fit["r2"] == pytest.approx(1.0)


def test_is_float():
    assert _is_float("1.5")
    assert _is_float("1,5")     # comma decimal, replaced before float()
    assert not _is_float("abc")
    assert not _is_float("")


def test_eff_t_start_uses_avg_duration_when_set():
    row = pd.Series({"t_start": 0.0, "t_end": 100.0, "avg_duration": 20.0})
    assert _eff_t_start(row) == pytest.approx(80.0)


def test_eff_t_start_falls_back_to_t_start():
    row = pd.Series({"t_start": 5.0, "t_end": 100.0, "avg_duration": np.nan})
    assert _eff_t_start(row) == pytest.approx(5.0)


def test_eff_t_start_none_when_t_start_missing():
    row = pd.Series({"t_start": np.nan, "t_end": 100.0, "avg_duration": np.nan})
    assert _eff_t_start(row) is None


def test_eff_t_start_ignores_nonpositive_avg_duration():
    row = pd.Series({"t_start": 5.0, "t_end": 100.0, "avg_duration": 0.0})
    assert _eff_t_start(row) == pytest.approx(5.0)
