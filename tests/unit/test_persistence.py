"""core/persistence.py round-trips settings + every mode's raw data through
_build_cfg_dict/_apply_cfg_dict (localStorage tier) and
_build_session_bundle/_apply_session_bundle (Export/Import JSON and Cloud
Sessions tier) — exercised here via json.dumps(..., default=str) +
json.loads, matching exactly how app.py serializes them, so a numpy-typed
leaf that default=str would silently stringify gets caught."""
import json

import pytest
from streamlit.testing.v1 import AppTest


def _in_app_test(body_fn, **kwargs):
    """Runs body_fn() inside a live Streamlit script context (so
    st.session_state behaves like the real app) and returns whatever it
    stashes in session_state["_result"]."""
    at = AppTest.from_function(body_fn, default_timeout=60, kwargs=kwargs)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at.session_state["_result"]


def test_cfg_dict_round_trip_settings_only():
    def _body():
        import json
        import streamlit as st
        from core.state import init_session_state
        from core.persistence import _build_cfg_dict, _apply_cfg_dict
        init_session_state()
        st.session_state.conc_unit = "mM"
        st.session_state.solid_conc_unit = "nM"
        cfg = _build_cfg_dict()
        raw = json.loads(json.dumps(cfg, default=str))
        # Simulate a fresh session before applying the loaded config.
        st.session_state.conc_unit = "SHOULD_BE_OVERWRITTEN"
        st.session_state.solid_conc_unit = "SHOULD_BE_OVERWRITTEN"
        _apply_cfg_dict(raw)
        st.session_state["_result"] = {
            "conc_unit": st.session_state.conc_unit,
            "solid_conc_unit": st.session_state.solid_conc_unit,
        }

    result = _in_app_test(_body)
    assert result["conc_unit"] == "mM"
    assert result["solid_conc_unit"] == "nM"


def test_session_bundle_round_trip_amp_files():
    def _body():
        import json
        import streamlit as st
        from core.state import init_session_state
        from core.persistence import _build_session_bundle, _apply_session_bundle
        import numpy as np
        import pandas as pd
        init_session_state()
        st.session_state.amp_files = [{
            "filename": "run.csv",
            "df": pd.DataFrame({"Time (s)": [0.0, 1.0], "Channel A (uA)": [1.0, 2.0]}),
            "channels": [{"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"}],
            "cpdf": pd.DataFrame({
                "Label": ["Blank"], "Concentration": [0.0], "Spike Vol": [np.nan],
                "Stock Conc": [np.nan], "t_start": [0.0], "t_end": [10.0],
                "avg_duration": [np.nan], "Baseline": [True],
            }),
        }]
        bundle = _build_session_bundle()
        raw = json.loads(json.dumps(bundle, default=str))
        init_session_state()   # reset to a blank session before restoring
        st.session_state.amp_files = []
        _apply_session_bundle(raw)
        f = st.session_state.amp_files[0]
        st.session_state["_result"] = {
            "filename": f["filename"],
            "current": f["df"]["Channel A (uA)"].tolist(),
            "cpdf_concentration": f["cpdf"]["Concentration"].tolist(),
            "cpdf_baseline": f["cpdf"]["Baseline"].tolist(),
        }

    result = _in_app_test(_body)
    assert result["filename"] == "run.csv"
    assert result["current"] == [1.0, 2.0]
    assert result["cpdf_concentration"] == [0.0]
    assert result["cpdf_baseline"] == [True]


def test_session_bundle_round_trip_solid_files_uses_solid_schema():
    def _body():
        import json
        import streamlit as st
        from core.state import init_session_state
        from core.persistence import _build_session_bundle, _apply_session_bundle
        import numpy as np
        import pandas as pd
        init_session_state()
        st.session_state.solid_files = [{
            "filename": "solid.csv",
            "df": pd.DataFrame({"Time (s)": [0.0, 1.0], "Potential (mV)": [-100.0, -105.0]}),
            "channels": [{"name": "E1", "tc": "Time (s)", "ic": "Potential (mV)"}],
            "cpdf": pd.DataFrame({
                "Label": ["Std 1"], "Concentration": [1.0], "t_start": [0.0],
                "t_end": [10.0], "avg_duration": [np.nan], "Reading_mV": [np.nan],
            }),
        }]
        bundle = _build_session_bundle()
        raw = json.loads(json.dumps(bundle, default=str))
        init_session_state()
        st.session_state.solid_files = []
        _apply_session_bundle(raw)
        f = st.session_state.solid_files[0]
        st.session_state["_result"] = {
            "filename": f["filename"],
            "columns": list(f["cpdf"].columns),
            "concentration": f["cpdf"]["Concentration"].tolist(),
        }

    result = _in_app_test(_body)
    assert result["filename"] == "solid.csv"
    assert "Baseline" not in result["columns"]     # solid schema, not amp's
    assert "Spike Vol" not in result["columns"]
    assert result["concentration"] == [1.0]


def test_session_bundle_round_trip_cv_runs_preserves_peaks():
    def _body():
        import json
        import streamlit as st
        from core.state import init_session_state
        from core.persistence import _build_session_bundle, _apply_session_bundle
        import pandas as pd
        init_session_state()
        st.session_state.cv_runs = [{
            "scan_rate": 50.0,
            "label": "50 mV/s",
            "filename": "cv1.csv",
            "df": pd.DataFrame({"V (V)": [-0.5, 0.0, 0.5], "I (uA)": [1.0, 2.0, 1.0]}),
            "channels": [{"name": "CH1", "vc": "V (V)", "ic": "I (uA)", "is_avg": False}],
            "peaks": {"CH1": {"anodic": [{"Ep": 0.1, "Ip": 5.0}], "cathodic": []}},
        }]
        bundle = _build_session_bundle()
        raw = json.loads(json.dumps(bundle, default=str))
        init_session_state()
        st.session_state.cv_runs = []
        _apply_session_bundle(raw)
        r = st.session_state.cv_runs[0]
        st.session_state["_result"] = {
            "scan_rate": r["scan_rate"],
            "peaks": r["peaks"],
        }

    result = _in_app_test(_body)
    assert result["scan_rate"] == pytest.approx(50.0)
    assert isinstance(result["scan_rate"], float)
    assert result["peaks"]["CH1"]["anodic"][0]["Ep"] == pytest.approx(0.1)


def test_session_bundle_round_trip_assay_state():
    def _body():
        import json
        import streamlit as st
        from core.state import init_session_state
        from core.persistence import _build_session_bundle, _apply_session_bundle
        import numpy as np
        import pandas as pd
        init_session_state()
        st.session_state.assay_plate = pd.DataFrame(
            np.full((8, 12), np.nan),
            index=pd.Index(list("ABCDEFGH"), name="Row"),
            columns=pd.Index(range(1, 13), name="Col"),
        )
        st.session_state.assay_plate.loc["A", 1] = 0.123
        st.session_state.assay_std_res = {
            "fit": {"type": "4pl", "a": np.float64(0.1), "b": np.float64(1.2),
                    "c": np.float64(5.0), "d": np.float64(2.0), "r2": np.float64(0.99)},
            "concs": [0.0, 1.0], "means": [0.0, 1.0],
        }
        bundle = _build_session_bundle()
        raw = json.loads(json.dumps(bundle, default=str))
        init_session_state()
        st.session_state.assay_plate = None
        st.session_state.assay_std_res = None
        _apply_session_bundle(raw)
        st.session_state["_result"] = {
            "plate_a1": float(st.session_state.assay_plate.loc["A", 1]),
            "fit_a": st.session_state.assay_std_res["fit"]["a"],
            "fit_a_is_float": isinstance(st.session_state.assay_std_res["fit"]["a"], float),
        }

    result = _in_app_test(_body)
    assert result["plate_a1"] == pytest.approx(0.123)
    assert result["fit_a"] == pytest.approx(0.1)
    # Regression: without _jsonify, a numpy.float64 fit param gets
    # stringified by json.dumps(default=str) and comes back as a string,
    # not a float — silently breaking downstream numeric formatting.
    assert result["fit_a_is_float"]
