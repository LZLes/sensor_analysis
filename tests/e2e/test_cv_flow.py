"""End-to-end: drives modes.cyclic_voltammetry.render() through AppTest."""
import pytest
from streamlit.testing.v1 import AppTest


def _cv_script():
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    import streamlit as st
    from core.state import init_session_state
    import modes.cyclic_voltammetry as mod

    init_session_state()
    v = np.concatenate([np.linspace(-0.5, 0.5, 200), np.linspace(0.5, -0.5, 200)])
    i = (5.0 * np.exp(-((v - 0.1) ** 2) / 0.0005)
         - 4.0 * np.exp(-((v + 0.1) ** 2) / 0.0005))
    df = pd.DataFrame({"V (V)": v, "I (uA)": i})
    channels = [{"name": "CH1", "vc": "V (V)", "ic": "I (uA)", "is_avg": False}]
    # Only seed on the very first script execution — AppTest.run() can
    # internally re-execute this script body more than once per logical
    # "run" (e.g. to settle widget defaults), and unconditionally
    # reassigning session_state here every time would silently wipe out
    # whatever the just-clicked button mutated in cv_runs.
    if not st.session_state.cv_runs:
        st.session_state.cv_runs = [{
            "scan_rate": 50.0, "label": "50 mV/s", "filename": "cv1.csv",
            "df": df, "channels": channels, "peaks": {},
        }]
    mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_cv_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_cv_renders_with_data_loaded(app):
    assert not app.exception


def _find_peaks_button(app):
    for b in app.button:
        if "Find Peaks" in (b.label or ""):
            return b
    raise AssertionError("Find Peaks button not found")


def test_cv_find_peaks_populates_peak_data(app):
    _find_peaks_button(app).click().run()
    assert not app.exception, [str(e) for e in app.exception]
    peaks = app.session_state.cv_runs[0]["peaks"]
    assert "CH1" in peaks
    assert len(peaks["CH1"]["anodic"]) >= 1
    assert len(peaks["CH1"]["cathodic"]) >= 1
    top_anodic = max(peaks["CH1"]["anodic"], key=lambda p: p["Ip"])
    assert top_anodic["Ep"] == pytest.approx(0.1, abs=0.05)


def test_cv_export_tab_renders_without_crashing(app):
    # Publication-quality export (segmented_control DPI picker) — same
    # widget-kwarg crash class as Amperometry/Assay, regression-checked here.
    assert not app.exception
    assert any("Publication-quality export" in str(md.value) for md in app.markdown)
