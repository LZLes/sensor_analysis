"""End-to-end: drives modes.assay.render() through AppTest."""
import pytest
from streamlit.testing.v1 import AppTest


def _assay_script():
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    import streamlit as st
    from core.state import init_session_state
    import modes.assay as mod

    init_session_state()
    # Only seed on the very first script execution — AppTest.run() can
    # internally re-execute this script body more than once per logical
    # "run", and unconditionally reassigning session_state here every time
    # would silently wipe out whatever a just-clicked button mutated.
    if st.session_state.assay_plate is None:
        plate = pd.DataFrame(
            np.full((8, 12), np.nan),
            index=pd.Index(list("ABCDEFGH"), name="Row"),
            columns=pd.Index(range(1, 13), name="Col"),
        )
        # Default assay_std_df: Blank/Std2../Std8 at Conc [0,1,2,5,10,20,50,100],
        # wells A{n}/B{n}/C{n} for sets S1/S2/S3 (see core/state.py).
        concs = [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]
        slope, intercept = 0.02, 0.1
        for col_n, conc in enumerate(concs, start=1):
            signal = intercept + slope * conc
            for row_letter in "ABC":
                plate.loc[row_letter, col_n] = signal
        # One sample well (not a standard) with a known concentration to
        # back-calculate: D1, conc=25 -> signal = intercept + slope*25.
        plate.loc["D", 1] = intercept + slope * 25.0
        st.session_state.assay_plate = plate
    mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_assay_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_assay_renders_with_plate_loaded(app):
    assert not app.exception


def test_assay_compute_standard_curve_recovers_linear_fit(app):
    app.button(key="assay_compute").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    res = app.session_state.assay_std_res
    assert res is not None
    assert res["fit"]["type"] == "linear"
    assert res["fit"]["slope"] == pytest.approx(0.02, abs=0.002)
    assert res["fit"]["r2"] > 0.99


def test_assay_results_tab_back_calculates_sample_concentration(app):
    app.button(key="assay_compute").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    df = None
    for d in app.dataframe:
        try:
            if "Conc" in "".join(str(c) for c in d.value.columns) and "Well" in d.value.columns:
                df = d.value
                break
        except Exception:
            continue
    assert df is not None, "Sample results table not found"
    row = df[df["Well"] == "D1"]
    assert not row.empty
    conc_col = [c for c in df.columns if c.startswith("Conc")][0]
    back_calc = float(row.iloc[0][conc_col])
    assert back_calc == pytest.approx(25.0, abs=1.0)
    assert row.iloc[0]["Flag"] == ""   # within standard range, not flagged
