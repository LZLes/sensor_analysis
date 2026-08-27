import numpy as np
import pytest

from modes.solid_state import (
    nernst_ideal_slope_mv, ideal_slope_in_signal_unit, nernstian_lod_fit,
)


def test_nernst_ideal_slope_mv_monovalent_25c_is_about_59():
    assert nernst_ideal_slope_mv(25.0, 1) == pytest.approx(59.16, abs=0.05)


def test_nernst_ideal_slope_mv_divalent_is_half_monovalent():
    mono = nernst_ideal_slope_mv(25.0, 1)
    di = nernst_ideal_slope_mv(25.0, 2)
    assert di == pytest.approx(mono / 2, rel=1e-9)


def test_nernst_ideal_slope_mv_varies_with_temperature():
    slope_25 = nernst_ideal_slope_mv(25.0, 1)
    slope_37 = nernst_ideal_slope_mv(37.0, 1)
    assert slope_37 > slope_25


def test_ideal_slope_in_signal_unit_converts_correctly():
    mv = nernst_ideal_slope_mv(25.0, 1)
    assert ideal_slope_in_signal_unit(25.0, 1, "mV") == pytest.approx(mv)
    assert ideal_slope_in_signal_unit(25.0, 1, "V") == pytest.approx(mv / 1000.0)
    assert ideal_slope_in_signal_unit(25.0, 1, "uV") == pytest.approx(mv * 1000.0)
    # Case/spacing-insensitive
    assert ideal_slope_in_signal_unit(25.0, 1, "Volts") == pytest.approx(mv / 1000.0)


def test_ideal_slope_in_signal_unit_unrecognized_returns_none():
    assert ideal_slope_in_signal_unit(25.0, 1, "arbitrary_unit") is None
    assert ideal_slope_in_signal_unit(25.0, 1, "pH") is None


def test_nernstian_lod_fit_recovers_two_segment_slopes():
    # Low-concentration plateau (slope ~0) then Nernstian regime (~59 mV/decade).
    # A tiny per-point offset on the "flat" segment avoids the degenerate
    # exactly-zero-variance-y case, where scipy's r (0/0) comes back NaN and
    # lin_reg rightly rejects that split candidate — real sensor noise
    # never lands on that knife-edge, so nudge the synthetic data off it.
    log_conc = np.array([-6, -5, -4, -3, -2, -1], dtype=float)
    ideal = nernst_ideal_slope_mv()
    flat_wobble = np.array([0.0, 0.05, -0.03, 0.0, 0.0, 0.0])
    e = np.where(log_conc < -3, -200.0, -200.0 + ideal * (log_conc - (-3))) + flat_wobble
    out = nernstian_lod_fit(log_conc, e)
    assert out["nernstian_segment"] is not None
    assert out["nernstian_segment"]["slope"] == pytest.approx(ideal, abs=1.0)
    assert out["low_segment"] is not None
    assert abs(out["low_segment"]["slope"]) < 5.0


def test_nernstian_lod_fit_too_few_points_returns_single_fit():
    log_conc = np.array([-3.0, -2.0, -1.0])
    e = np.array([-100.0, -50.0, 0.0])
    out = nernstian_lod_fit(log_conc, e)
    assert out["low_segment"] is None
    assert out["nernstian_segment"] is not None
    assert np.isnan(out["lod_log10"])


def test_nernstian_lod_fit_lod_is_finite_for_clean_two_segment_data():
    log_conc = np.array([-6, -5, -4, -3, -2, -1], dtype=float)
    ideal = nernst_ideal_slope_mv()
    flat_wobble = np.array([0.0, 0.05, -0.03, 0.0, 0.0, 0.0])
    e = np.where(log_conc < -3, -200.0, -200.0 + ideal * (log_conc - (-3))) + flat_wobble
    out = nernstian_lod_fit(log_conc, e)
    assert np.isfinite(out["lod_log10"])
    assert np.isfinite(out["lod_conc"])
    assert out["lod_conc"] == pytest.approx(10.0 ** out["lod_log10"])
