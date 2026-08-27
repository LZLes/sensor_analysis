"""End-to-end: drives modes.solid_state.render() through AppTest."""
import pytest
from streamlit.testing.v1 import AppTest


def _solid_script():
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    import streamlit as st
    from core.state import init_session_state
    import modes.solid_state as mod

    init_session_state()
    rng = np.random.default_rng(3)
    concs = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1]
    ideal = 59.16
    rows_t, rows_v = [], []
    t0 = 0.0
    for c in concs:
        e = -200 + ideal * np.log10(c)
        for dt in range(60):
            rows_t.append(t0 + dt)
            rows_v.append(e + 0.05 * rng.standard_normal())
        t0 += 60
    df = pd.DataFrame({"Time (s)": rows_t, "Potential (mV)": rows_v})
    channels = [{"name": "Electrode 1", "tc": "Time (s)", "ic": "Potential (mV)"}]
    cpdf = pd.DataFrame({
        "Label":         [f"Std {i+1}" for i in range(len(concs))],
        "Concentration": concs,
        "t_start":       [i * 60.0 for i in range(len(concs))],
        "t_end":         [i * 60.0 + 60.0 for i in range(len(concs))],
        "avg_duration":  [30.0] * len(concs),
        "Reading_mV":    [np.nan] * len(concs),
    })
    # Only seed on the very first script execution — see test_cv_flow.py's
    # equivalent comment for why this must be idempotent.
    if not st.session_state.solid_files:
        st.session_state.solid_files = [
            {"filename": "solid.csv", "df": df, "channels": channels, "cpdf": cpdf}
        ]
    mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_solid_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_solid_state_renders_with_data_loaded(app):
    assert not app.exception


def _compute_button(app):
    for b in app.button:
        if b.key and "Compute Calibration" in (b.key or ""):
            return b
    raise AssertionError("Compute Calibration button not found")


def test_solid_state_compute_calibration_recovers_nernstian_slope(app):
    _compute_button(app).click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state.solid_cal_results is not None
    res = app.session_state.solid_cal_results["results"]
    assert "Electrode 1" in res
    ch = res["Electrode 1"]
    assert ch["nernstian_segment"] is not None
    assert ch["nernstian_segment"]["slope"] == pytest.approx(59.16, abs=2.0)
    # Default z=1, 25C, mV unit -> "% of ideal" must be computable, not None
    # (regression for the unit-mismatch bug: ideal must not silently fail
    # to convert for the default, recognized "mV" unit).
    assert ch["pct_of_ideal_nernstian"] is not None
    assert ch["pct_of_ideal_nernstian"] == pytest.approx(100.0, abs=5.0)


def test_solid_state_stats_table_shows_after_compute(app):
    _compute_button(app).click().run()
    assert not app.exception
    assert any("Statistics" in str(sh.value) for sh in app.subheader)
