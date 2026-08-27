"""End-to-end: drives modes.amperometry.render() through Streamlit's real
AppTest script-runner (button clicks, form submits, reruns) rather than
calling internal functions directly — catches the class of bug unit tests
can't (e.g. a widget kwarg removed by a Streamlit upgrade, which only
surfaces when the widget actually renders)."""
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


def _amp_script():
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    import streamlit as st
    from core.state import init_session_state
    import modes.amperometry as mod

    init_session_state()
    t = np.arange(0, 450, 1.0)
    base = 1.0 + 0.001 * np.sin(t / 5.0)
    step = np.where(t >= 300, 1.0, np.where(t >= 150, 0.5, 0.0))
    df = pd.DataFrame({"Time (s)": t, "Channel A (uA)": base + step})
    channels = [{"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"}]
    # 2 non-baseline rows minimum — piecewise_fit needs >= 2 points once the
    # baseline point is excluded from the fit, or Statistics never renders.
    cpdf = pd.DataFrame({
        "Label":         ["Blank", "Step 1", "Step 2"],
        "Concentration": [0.0, 0.5, 1.0],
        "Spike Vol":     [np.nan, np.nan, np.nan],
        "Stock Conc":    [np.nan, np.nan, np.nan],
        "t_start":       [0.0, 160.0, 310.0],
        "t_end":         [140.0, 290.0, 440.0],
        "avg_duration":  [np.nan, np.nan, np.nan],
        "Baseline":      [True, False, False],
    })
    # Only seed on the very first script execution — AppTest.run() can
    # internally re-execute this script body more than once per logical
    # "run", and unconditionally reassigning session_state here every time
    # would silently wipe out whatever a just-clicked button mutated.
    if not st.session_state.amp_files:
        st.session_state.amp_files = [{"filename": "run.csv", "df": df, "channels": channels, "cpdf": cpdf}]
    mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_amp_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_amperometry_renders_with_data_loaded(app):
    assert not app.exception


def test_amperometry_export_tab_renders_without_crashing(app):
    # Publication-quality export section (segmented_control DPI picker) is
    # only reached when amp_files is non-empty — regression for the
    # `required=True` kwarg Streamlit removed, which only crashes here.
    assert not app.exception
    assert any("Publication-quality export" in str(md.value) for md in app.markdown)


def test_amperometry_compute_calibration_populates_results(app):
    submit = app.button(key="FormSubmitter:amp_cal_form_0-Compute Calibration")
    submit.click().run()
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state.cal_results is not None
    res = app.session_state.cal_results["results"]
    assert "Channel A" in res
    ch = res["Channel A"]
    # Blank subtracted: delta_i for the blank row itself must be 0.
    baseline_idx = ch["baselines"].index(True)
    assert ch["delta_i"][baseline_idx] == pytest.approx(0.0, abs=1e-9)
    # Step 1 (0.5 mM) / Step 2 (1.0 mM) should show deflections close to
    # the synthetic 0.5/1.0 uA steps injected above.
    assert ch["delta_i"][1] == pytest.approx(0.5, abs=0.05)
    assert ch["delta_i"][2] == pytest.approx(1.0, abs=0.05)


def test_amperometry_stats_table_shows_after_compute(app):
    app.button(key="FormSubmitter:amp_cal_form_0-Compute Calibration").click().run()
    assert not app.exception
    assert any("Statistics" in str(sh.value) for sh in app.subheader)
