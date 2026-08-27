import numpy as np
import pandas as pd
import pytest

from modes.amperometry import (
    piecewise_fit, _hinge_fit, _apply_avg_window,
    _spike_vol_for_targets, _preset_cpdf_amp,
)


def test_piecewise_fit_single_segment_is_plain_linear():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = 2.0 * x + 1.0
    out = piecewise_fit(x, y, n_seg=1)
    assert len(out["segments"]) == 1
    assert out["breakpoints"] == []
    assert out["segments"][0]["slope"] == pytest.approx(2.0)


def test_piecewise_fit_two_segments_recovers_known_breakpoint():
    x = np.linspace(0, 10, 40)
    bp = 5.0
    y = np.where(x < bp, 2.0 * x, 2.0 * bp + 0.2 * (x - bp))
    out = piecewise_fit(x, y, n_seg=2)
    assert len(out["segments"]) == 2
    assert out["breakpoints"][0] == pytest.approx(bp, abs=1.0)
    assert out["segments"][0]["slope"] == pytest.approx(2.0, abs=0.1)
    assert out["segments"][1]["slope"] == pytest.approx(0.2, abs=0.1)


def test_piecewise_fit_segments_meet_at_breakpoint_continuously():
    x = np.linspace(0, 10, 40)
    y = np.where(x < 5, 2.0 * x, 10.0 + 0.5 * (x - 5)) + 0.01 * np.sin(x)
    out = piecewise_fit(x, y, n_seg=2)
    seg0, seg1 = out["segments"]
    bp = out["breakpoints"][0]
    y0 = seg0["slope"] * bp + seg0["intercept"]
    y1 = seg1["slope"] * bp + seg1["intercept"]
    assert y0 == pytest.approx(y1, abs=1e-6)


def test_piecewise_fit_too_few_points_falls_back_to_single():
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([0.0, 1.0, 2.0])
    out = piecewise_fit(x, y, n_seg=3)
    assert len(out["segments"]) == 1


def test_piecewise_fit_empty_input():
    out = piecewise_fit(np.array([]), np.array([]), n_seg=1)
    assert out == {"segments": [], "breakpoints": []}


def test_hinge_fit_mismatched_lengths_returns_none_and_sentinel_ssr():
    coef, ssr = _hinge_fit(np.array([1.0, 2.0]), np.array([1.0]), [])
    assert coef is None
    assert ssr == 1e18


def test_apply_avg_window_noop_without_avg_duration():
    cpdf = pd.DataFrame({
        "Label": ["Blank", "Step 1"],
        "Concentration": [0.0, 5.0],
        "Spike Vol": [np.nan, np.nan],
        "Stock Conc": [np.nan, np.nan],
        "t_start": [0.0, 10.0],
        "t_end": [10.0, 20.0],
        "avg_duration": [np.nan, np.nan],
        "Baseline": [True, False],
    })
    out = _apply_avg_window(cpdf)
    assert out["t_start"].tolist() == [0.0, 10.0]


def test_apply_avg_window_overrides_t_start():
    cpdf = pd.DataFrame({
        "Label": ["Step 1"],
        "Concentration": [1.0],
        "Spike Vol": [np.nan],
        "Stock Conc": [np.nan],
        "t_start": [0.0],
        "t_end": [100.0],
        "avg_duration": [20.0],
        "Baseline": [False],
    })
    out = _apply_avg_window(cpdf)
    assert out["t_start"].iloc[0] == pytest.approx(80.0)


def test_spike_vol_for_targets_requires_stock_stronger_than_target():
    with pytest.raises(ValueError):
        _spike_vol_for_targets([50.0, 100.0], stock_conc=100.0, initial_volume=1.0)


def test_spike_vol_for_targets_reproduces_targets():
    targets = [10.0, 25.0, 50.0]
    spikes = _spike_vol_for_targets(targets, stock_conc=1000.0, initial_volume=1.0)
    vol, mass = 1.0, 0.0
    got = []
    for sv in spikes:
        vol += sv
        mass += sv * 1000.0
        got.append(mass / vol)
    for g, t in zip(got, targets):
        assert g == pytest.approx(t, rel=1e-6)


def test_preset_cpdf_amp_cumulative_concentration_and_blank_row():
    df = _preset_cpdf_amp(
        increments=[1.0, 1.0, 2.0], start=100.0, interval=50.0,
        include_blank=True, stock_conc=1000.0, initial_volume=1.0,
        avg_window=10.0,
    )
    assert df["Label"].tolist() == ["Blank", "Step 1", "Step 2", "Step 3"]
    assert df["Concentration"].tolist() == [0.0, 1.0, 2.0, 4.0]
    assert df["Baseline"].tolist() == [True, False, False, False]
    assert df["t_start"].tolist() == [0.0, 100.0, 150.0, 200.0]
    assert np.isnan(df["Spike Vol"].iloc[0])   # blank row has no spike


def test_preset_cpdf_amp_without_blank():
    df = _preset_cpdf_amp(
        increments=[5.0], start=60.0, interval=30.0,
        include_blank=False, stock_conc=1000.0, initial_volume=1.0,
        avg_window=10.0,
    )
    assert df["Label"].tolist() == ["Step 1"]
    assert df["Baseline"].tolist() == [False]


def test_preset_cpdf_amp_zero_stock_conc_skips_backsolve():
    # Stock Conc of 0 means "don't back-solve Spike Vol" — Concentration
    # still comes straight from increments, but Spike Vol/Stock Conc stay NaN.
    df = _preset_cpdf_amp(
        increments=[1.0, 1.0, 2.0], start=100.0, interval=50.0,
        include_blank=True, stock_conc=0.0, initial_volume=1.0,
        avg_window=10.0,
    )
    assert df["Concentration"].tolist() == [0.0, 1.0, 2.0, 4.0]
    assert df["Spike Vol"].isna().all()
    assert df["Stock Conc"].isna().all()


def test_preset_cpdf_amp_per_step_intervals():
    df = _preset_cpdf_amp(
        increments=[1.0, 1.0, 2.0], start=100.0, interval=[50.0, 100.0, 25.0],
        include_blank=False, stock_conc=0.0, initial_volume=1.0,
        avg_window=10.0,
    )
    assert df["t_start"].tolist() == [100.0, 150.0, 250.0]
    assert df["t_end"].tolist() == [150.0, 250.0, 275.0]


def test_preset_cpdf_amp_interval_count_mismatch_raises():
    with pytest.raises(ValueError):
        _preset_cpdf_amp(
            increments=[1.0, 1.0, 2.0], start=100.0, interval=[50.0, 100.0],
            include_blank=False, stock_conc=0.0, initial_volume=1.0,
            avg_window=10.0,
        )
