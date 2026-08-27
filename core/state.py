"""Session-state bootstrap: one flat list of defaults for all 4 modes,
kept centralized (not per-mode) so switching SS.mode never KeyErrors on
a key only some other mode's file would otherwise define."""

import pandas as pd
import streamlit as st

SS = st.session_state


def init_session_state() -> None:
    for _k, _v in [
        ("df", None),
        ("channels", []),
        ("amp_files", []),       # [{filename, df, channels}] — multi-file amperometry
        ("solid_files", []),     # [{filename, df, channels}] — multi-file solid-state (potentiometric)
        ("solid_cal_results", None),
        ("cal_results", None),
        ("cal_fig", None),
        ("solid_cal_fig", None),
        # ts_fig/ts_visible are intentionally NOT declared here — they're
        # namespaced per mode as f"{files_key}_ts_fig"/f"{files_key}_ts_vis_ms"
        # (set in core/shared_tabs.py) so Amperometry and Solid-State don't
        # silently share one Time Series plot/selection between modes.
        ("conc_unit", "mM"),          # Amperometry's concentration unit
        ("solid_conc_unit", "M"),     # Solid-State's own concentration unit — kept
                                       # separate from conc_unit since the two
                                       # modes' typical concentration ranges/units
                                       # differ and shouldn't leak into each other
        ("cur_unit", "µA"),
        ("solid_unit", "mV"),
        ("vol_unit", "µL"),
        ("initial_volume", 1.0),
        ("smooth_method", "None"),
        ("smooth_window", 11),
        ("smooth_polyorder", 2),
        ("ts_y_auto", True),
        ("ts_y_min",  None),
        ("ts_y_max",  None),
        # Shared / CV
        ("mode",       "Amperometry"),
        ("volt_unit",  "V"),
        ("cv_cur_unit","µA"),
        ("cv_sr_unit", "mV/s"),
        ("cv_runs",    []),          # [{scan_rate, label, filename, df, channels, peaks}]
        # Assay
        ("assay_plate",     None),
        ("assay_sig_unit",  "Abs"),
        ("assay_conc_unit", "µM"),
        ("assay_std_res",   None),
    ]:
        if _k not in SS:
            SS[_k] = _v
    
    if "assay_std_df" not in SS:
        SS["assay_std_df"] = pd.DataFrame({
            "Label": ["Blank", "Std 2", "Std 3", "Std 4", "Std 5", "Std 6", "Std 7", "Std 8"],
            "Conc":  [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0],
            "S1":    ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
            "S2":    ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"],
            "S3":    ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"],
        })
    
    if "assay_sample_df" not in SS:
        SS["assay_sample_df"] = pd.DataFrame(
            {"Well": pd.Series([], dtype=str), "Label": pd.Series([], dtype=str)}
        )
