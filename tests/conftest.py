"""Shared fixtures for the test suite.

Runs against the real app code (core/, modes/) with no mocking of pandas/
numpy/scipy/streamlit — only Streamlit's own AppTest harness stands in for
a browser. AppTest.from_function scripts are self-contained (matching its
"must be runnable in isolation" requirement) and deliberately bypass
app.py's sidebar/localStorage-loading code, which hangs under AppTest
because the streamlit_local_storage custom component never resolves
without a real browser round-trip — testing modes.<name>.render() directly
sidesteps that without weakening what's under test.
"""
import io
import os

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DATA_DIR = os.path.join(PROJECT_ROOT, "sample_data")


def _patch_apptest_segmented_control_bug() -> None:
    """Streamlit 1.52's AppTest has its own bug, unrelated to this app:
    ButtonGroup.indices (streamlit/testing/v1/element_tree.py) does
    `[... for v in self.value]`, assuming .value is always a list — but
    st.segmented_control's default selection_mode="single" (used
    throughout this app) makes .value a bare scalar, so ANY app using a
    single-select segmented_control crashes AppTest with
    `TypeError: 'int' object is not iterable` the moment a second .run()
    re-serializes the widget tree (e.g. after a button click) while that
    widget is on screen. Reproduced with a 3-line script containing only
    st.segmented_control + st.button — confirmed independent of this
    app's code. Patched here (test-harness only, real browser users never
    hit this code path) so e2e tests can drive multi-step flows normally."""
    from streamlit.testing.v1 import element_tree

    def _patched_indices(self):
        v = self.value
        if not isinstance(v, (list, tuple)):
            v = [] if v is None else [v]
        return [self.options.index(self.format_func(x)) for x in v]

    element_tree.ButtonGroup.indices = property(_patched_indices)


_patch_apptest_segmented_control_bug()


def make_mode_apptest(mode_module: str):
    """Returns a fresh AppTest for the given mode module
    ('modes.amperometry', 'modes.solid_state', 'modes.cyclic_voltammetry',
    'modes.assay'), rendered once with default (empty) session state."""
    from streamlit.testing.v1 import AppTest

    if mode_module == "modes.amperometry":
        def _script():
            import matplotlib
            matplotlib.use("Agg")
            from core.state import init_session_state
            import modes.amperometry as mod
            init_session_state()
            mod.render()
    elif mode_module == "modes.solid_state":
        def _script():
            import matplotlib
            matplotlib.use("Agg")
            from core.state import init_session_state
            import modes.solid_state as mod
            init_session_state()
            mod.render()
    elif mode_module == "modes.cyclic_voltammetry":
        def _script():
            import matplotlib
            matplotlib.use("Agg")
            from core.state import init_session_state
            import modes.cyclic_voltammetry as mod
            init_session_state()
            mod.render()
    elif mode_module == "modes.assay":
        def _script():
            import matplotlib
            matplotlib.use("Agg")
            from core.state import init_session_state
            import modes.assay as mod
            init_session_state()
            mod.render()
    else:
        raise ValueError(mode_module)

    at = AppTest.from_function(_script, default_timeout=60)
    return at


@pytest.fixture
def amp_sample_files():
    """Two synthetic amperometry runs (2 channels each), matching the
    shape modes.amperometry._load_sample_data() builds from sample_data/."""
    from core.calibration_table import _default_cpdf

    def _one(name, seed):
        rng = np.random.default_rng(seed)
        t = np.arange(0, 300, 1.0)
        base = 1.0 + 0.01 * rng.standard_normal(t.size)
        step = np.where(t >= 150, 0.5, 0.0)
        df = pd.DataFrame({
            "Time (s)": t,
            "Channel A (uA)": base + step + 0.005 * rng.standard_normal(t.size),
            "Channel B (uA)": base * 1.1 + step + 0.005 * rng.standard_normal(t.size),
        })
        channels = [
            {"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"},
            {"name": "Channel B", "tc": "Time (s)", "ic": "Channel B (uA)"},
        ]
        cpdf = pd.DataFrame({
            "Label":         ["Blank", "Step 1"],
            "Concentration": [0.0, 0.5],
            "Spike Vol":     [np.nan, np.nan],
            "Stock Conc":    [np.nan, np.nan],
            "t_start":       [0.0, 160.0],
            "t_end":         [140.0, 290.0],
            "avg_duration":  [np.nan, np.nan],
            "Baseline":      [True, False],
        })
        return {"filename": name, "df": df, "channels": channels, "cpdf": cpdf}

    return [_one("run_a.csv", 1), _one("run_b.csv", 2)]


@pytest.fixture
def solid_sample_file():
    """One synthetic potentiometric run with a clean log-linear (Nernstian)
    response, for Solid-State unit/e2e tests."""
    rng = np.random.default_rng(3)
    concs = [1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1]
    ideal = 59.16  # mV/decade at 25C, z=1
    rows_t, rows_v = [], []
    t0 = 0.0
    for c in concs:
        e = -200 + ideal * np.log10(c)
        for dt in range(60):
            rows_t.append(t0 + dt)
            rows_v.append(e + 0.2 * rng.standard_normal())
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
    return {"filename": "solid_run.csv", "df": df, "channels": channels, "cpdf": cpdf}


@pytest.fixture
def cv_csv_bytes():
    """A minimal synthetic CV trace as raw CSV bytes (Standard CSV format:
    Potential, Current header), triangle sweep with one redox peak pair."""
    v = np.concatenate([np.linspace(-0.5, 0.5, 200), np.linspace(0.5, -0.5, 200)])
    i = 5.0 * np.exp(-((v - 0.1) ** 2) / 0.002) - 4.0 * np.exp(-((v + 0.1) ** 2) / 0.002)
    i += 0.5 * np.sin(np.linspace(0, 20, v.size))  # small baseline wiggle, not noise-critical
    df = pd.DataFrame({"Potential (V)": v, "Current (uA)": i})
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def plate_csv_text():
    """A minimal 8x12 plate-reader CSV: rows A-H, 12 numeric columns each."""
    rng = np.random.default_rng(4)
    lines = []
    for r in "ABCDEFGH":
        vals = 0.1 + 0.05 * rng.standard_normal(12)
        lines.append(r + "," + ",".join(f"{v:.4f}" for v in vals))
    return "\n".join(lines)
