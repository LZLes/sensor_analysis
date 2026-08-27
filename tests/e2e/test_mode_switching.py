"""Regression tests for issues #2/#3/#4: Amperometry and Solid-State used to
share conc_unit / cal_editor_version / ts_fig / ts_visible as flat global
session-state keys, so configuring one mode silently leaked into the other.
Drives both modes' render() in sequence within one session (matching how
app.py's single script dispatches whichever mode is selected) to prove they
no longer collide."""
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


def _both_modes_script():
    import matplotlib
    matplotlib.use("Agg")
    import streamlit as st
    from core.state import init_session_state
    import modes.amperometry as amp_mod
    import modes.solid_state as solid_mod

    init_session_state()
    # Set distinct concentration units per mode, as a user would via each
    # mode's own Import & Configure tab. Guarded so AppTest's internal
    # re-execution of this script body doesn't reset them mid-flow.
    if not st.session_state.get("_seeded"):
        st.session_state.conc_unit = "mM"
        st.session_state.solid_conc_unit = "nM"
        st.session_state["_seeded"] = True

    amp_mod.render()
    solid_mod.render()


@pytest.fixture
def app():
    at = AppTest.from_function(_both_modes_script, default_timeout=60)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_conc_unit_does_not_leak_between_modes(app):
    assert app.session_state.conc_unit == "mM"
    assert app.session_state.solid_conc_unit == "nM"
    assert app.session_state.conc_unit != app.session_state.solid_conc_unit


def test_ts_fig_and_ts_visible_are_namespaced_per_mode(app):
    # Neither a flat SS.ts_fig nor SS.ts_visible should exist anymore —
    # only the per-mode namespaced keys.
    assert "ts_fig" not in app.session_state
    assert "ts_visible" not in app.session_state


def test_cal_editor_version_is_namespaced_per_mode(app):
    # The old shared flat key must be gone — each mode now tracks its own
    # counter under a files_key-prefixed name instead.
    assert "cal_editor_version" not in app.session_state
