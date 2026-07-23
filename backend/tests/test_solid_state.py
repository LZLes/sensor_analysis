import numpy as np
import pytest

from app.analysis.solid_state import nernst_ideal_slope_mv, nernstian_lod_fit


def test_nernst_ideal_slope_25c_monovalent():
    # Textbook value: ~59.16 mV/decade for a monovalent ion at 25 C
    assert nernst_ideal_slope_mv(25.0, 1) == pytest.approx(59.16, abs=0.05)


def test_nernst_ideal_slope_divalent_is_half():
    slope_1 = nernst_ideal_slope_mv(25.0, 1)
    slope_2 = nernst_ideal_slope_mv(25.0, 2)
    assert slope_2 == pytest.approx(slope_1 / 2.0, rel=1e-9)


def test_nernst_ideal_slope_increases_with_temperature():
    assert nernst_ideal_slope_mv(37.0, 1) > nernst_ideal_slope_mv(25.0, 1)


def test_nernstian_lod_fit_recovers_known_intersection():
    # Construct synthetic data: a flattened low-concentration regime
    # (slope ~5 mV/decade) and a Nernstian high-concentration regime
    # (slope ~59 mV/decade), analytically intersecting at log_conc = -4.
    # E_low(x)    = 5*x + b_low
    # E_nernst(x) = 59*x + b_nernst
    # At intersection x0=-4: 5*(-4)+b_low = 59*(-4)+b_nernst
    slope_low, slope_nernst = 5.0, 59.0
    x0 = -4.0
    b_low = 100.0
    b_nernst = b_low + (slope_low - slope_nernst) * x0   # solve for b_nernst at the same y

    log_conc = np.array([-6.0, -5.5, -5.0, -4.5, -3.5, -3.0, -2.5, -2.0])
    potential = np.where(
        log_conc <= x0,
        slope_low * log_conc + b_low,
        slope_nernst * log_conc + b_nernst,
    )

    result = nernstian_lod_fit(log_conc, potential)
    assert result["low_segment"] is not None
    assert result["nernstian_segment"] is not None
    assert result["low_segment"]["slope"] == pytest.approx(slope_low, abs=1.0)
    assert result["nernstian_segment"]["slope"] == pytest.approx(slope_nernst, abs=1.0)
    assert result["lod_log10"] == pytest.approx(x0, abs=0.6)
    assert result["lod_conc"] == pytest.approx(10 ** x0, rel=0.3)


def test_nernstian_lod_fit_lod_not_forced_onto_a_data_point():
    # The whole point of this function vs. piecewise_fit: the LOD can and
    # generally will fall strictly between two sampled log-concentrations.
    slope_low, slope_nernst = 2.0, 55.0
    x0 = -4.3   # deliberately NOT one of the sampled points below
    b_low = 50.0
    b_nernst = b_low + (slope_low - slope_nernst) * x0

    log_conc = np.array([-6.0, -5.0, -4.5, -4.0, -3.5, -3.0])
    potential = np.where(
        log_conc <= x0,
        slope_low * log_conc + b_low,
        slope_nernst * log_conc + b_nernst,
    )
    result = nernstian_lod_fit(log_conc, potential)
    assert not any(np.isclose(result["lod_log10"], v) for v in log_conc)


def test_nernstian_lod_fit_too_few_points_returns_single_fit_no_crash():
    log_conc = np.array([-5.0, -4.0, -3.0])
    potential = np.array([100.0, 150.0, 200.0])
    result = nernstian_lod_fit(log_conc, potential)
    assert result["low_segment"] is None
    assert result["nernstian_segment"] is not None
    assert np.isnan(result["lod_log10"])
    assert np.isnan(result["lod_conc"])


def test_nernstian_lod_fit_rejects_nan_and_inf_inputs_gracefully():
    # Caller is responsible for filtering Concentration <= 0 before calling
    # (log10(0) = -inf), but the function must not crash if a NaN/inf slips
    # through — it should simply drop those points.
    log_conc = np.array([-6.0, -5.0, -np.inf, -4.0, np.nan, -3.0, -2.0, -1.0])
    potential = np.array([10.0, 20.0, 999.0, 40.0, 999.0, 150.0, 200.0, 250.0])
    result = nernstian_lod_fit(log_conc, potential)
    # Should not raise, and should still produce a result from the 6 valid points
    assert result["nernstian_segment"] is not None
