"""End-to-end Export-JSON -> Import-JSON round trip across all 4 modes,
driven through each mode's real render() (not just core.persistence's
functions directly, as tests/unit/test_persistence.py does) so the exact
data shapes each mode actually produces are what gets serialized."""
import pytest
from streamlit.testing.v1 import AppTest


def _full_session_script():
    import json
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    import pandas as pd
    import streamlit as st
    from core.state import init_session_state
    from core.persistence import _build_session_bundle, _apply_session_bundle
    import modes.amperometry as amp_mod
    import modes.solid_state as solid_mod
    import modes.cyclic_voltammetry as cv_mod
    import modes.assay as assay_mod

    init_session_state()

    if not st.session_state.get("_seeded"):
        st.session_state["_seeded"] = True

        st.session_state.amp_files = [{
            "filename": "amp.csv",
            "df": pd.DataFrame({"Time (s)": [0.0, 1.0], "Channel A (uA)": [1.0, 2.0]}),
            "channels": [{"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"}],
            "cpdf": pd.DataFrame({
                "Label": ["Blank"], "Concentration": [0.0], "Spike Vol": [np.nan],
                "Stock Conc": [np.nan], "t_start": [0.0], "t_end": [10.0],
                "avg_duration": [np.nan], "Baseline": [True],
            }),
        }]
        st.session_state.solid_files = [{
            "filename": "solid.csv",
            "df": pd.DataFrame({"Time (s)": [0.0, 1.0], "Potential (mV)": [-100.0, -105.0]}),
            "channels": [{"name": "E1", "tc": "Time (s)", "ic": "Potential (mV)"}],
            "cpdf": pd.DataFrame({
                "Label": ["Std 1"], "Concentration": [1.0], "t_start": [0.0],
                "t_end": [10.0], "avg_duration": [np.nan], "Reading_mV": [np.nan],
            }),
        }]
        st.session_state.cv_runs = [{
            "scan_rate": 50.0, "label": "50 mV/s", "filename": "cv1.csv",
            "df": pd.DataFrame({"V (V)": [-0.5, 0.0, 0.5], "I (uA)": [1.0, 2.0, 1.0]}),
            "channels": [{"name": "CH1", "vc": "V (V)", "ic": "I (uA)", "is_avg": False}],
            "peaks": {"CH1": {"anodic": [{"Ep": 0.1, "Ip": 5.0}], "cathodic": []}},
        }]
        st.session_state.assay_plate = pd.DataFrame(
            np.full((8, 12), np.nan),
            index=pd.Index(list("ABCDEFGH"), name="Row"),
            columns=pd.Index(range(1, 13), name="Col"),
        )
        st.session_state.assay_plate.loc["A", 1] = 0.5

        # Export -> Import round trip (exactly what the sidebar buttons do
        # in app.py, minus the actual file download/upload widgets).
        bundle = _build_session_bundle()
        raw = json.loads(json.dumps(bundle, default=str))
        init_session_state()
        st.session_state.amp_files = []
        st.session_state.solid_files = []
        st.session_state.cv_runs = []
        st.session_state.assay_plate = None
        _apply_session_bundle(raw)

    # Now render every mode with the RESTORED state — if any mode's
    # render() chokes on the restored data shape, this surfaces it.
    amp_mod.render()
    solid_mod.render()
    cv_mod.render()
    assay_mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_full_session_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_all_four_modes_render_after_restore(app):
    assert not app.exception


def test_amp_files_restored(app):
    assert len(app.session_state.amp_files) == 1
    assert app.session_state.amp_files[0]["filename"] == "amp.csv"


def test_solid_files_restored_with_solid_schema(app):
    assert len(app.session_state.solid_files) == 1
    f = app.session_state.solid_files[0]
    assert f["filename"] == "solid.csv"
    assert "Baseline" not in f["cpdf"].columns


def test_cv_runs_restored_with_peaks(app):
    assert len(app.session_state.cv_runs) == 1
    r = app.session_state.cv_runs[0]
    assert r["scan_rate"] == pytest.approx(50.0)
    assert r["peaks"]["CH1"]["anodic"][0]["Ep"] == pytest.approx(0.1)


def test_assay_plate_restored(app):
    assert app.session_state.assay_plate is not None
    assert float(app.session_state.assay_plate.loc["A", 1]) == pytest.approx(0.5)
