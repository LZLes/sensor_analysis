"""
Session persistence for the macOS app — Tier 2 (Export/Import JSON) only.
Tier 1 (settings-only "Save") is macos_app/ui/settings.py's QSettings
wrapper; Tier 3 (Google Drive Cloud Sessions) isn't built for this app per
the migration plan.

build_session_bundle()/apply_session_bundle() produce and consume the
EXACT same dict shape as core/persistence.py's _build_session_bundle()/
_apply_session_bundle() — verified by round-tripping a bundle through
both implementations in each direction (see the test suite) — so a
session exported from either app opens correctly in the other. Not a
copy of those functions: SS = st.session_state there is a global proxy
these can't (and shouldn't) touch, so this operates on an AppState
instance instead, but the resulting dict keys/shapes are kept identical
by construction.

Reuses core/persistence.py's _jsonify/_plate_df_to_csv/_plate_df_from_csv
and core/calibration_table.py's _cpdf_from_records/_solid_cpdf_from_records
directly, unmodified — all pure, no Streamlit calls despite living in
files that import streamlit at module level (see
macos_app/requirements-macos.txt's note on this).
"""

from __future__ import annotations

import io

import pandas as pd

from core.calibration_table import _cpdf_from_records, _solid_cpdf_from_records
from core.persistence import _jsonify, _plate_df_from_csv, _plate_df_to_csv
from macos_app.ui.app_state import AppState
from macos_app.ui.undo_commands import FilesListCommand, SetFieldCommand

_CFG_FIELDS = [
    "conc_unit", "solid_conc_unit", "cur_unit", "solid_unit", "volt_unit",
    "cv_cur_unit", "cv_sr_unit", "vol_unit", "initial_volume",
    "smooth_method", "smooth_window", "smooth_polyorder",
    "assay_sig_unit", "assay_conc_unit",
]


def build_cfg_dict(app_state: AppState) -> dict:
    """Same shape as core/persistence.py's _build_cfg_dict()."""
    return {field: app_state.get_field(field) for field in _CFG_FIELDS}


def apply_cfg_dict(app_state: AppState, d: dict, macro_text: str = "Apply settings") -> None:
    """Same semantics as core/persistence.py's _apply_cfg_dict() (only
    known keys applied, others ignored), pushed as one undo step covering
    every field it touches. Pushes an actual SetFieldCommand per field
    rather than calling AppState.set_field() directly — set_field() just
    mutates state and emits a signal, it doesn't go through the undo
    stack, so wrapping bare set_field() calls in beginMacro/endMacro would
    produce an empty (no-op) undo entry. Qt macros nest cleanly: when this
    runs inside another beginMacro/endMacro (see apply_session_bundle),
    these commands become children of that outer macro instead of a
    separate step."""
    stack = app_state.undo_stack
    stack.beginMacro(macro_text)
    try:
        for field in _CFG_FIELDS:
            if field not in d:
                continue
            value = d[field]
            if field in ("initial_volume",):
                value = float(value)
            elif field in ("smooth_window", "smooth_polyorder"):
                value = int(value)
            stack.push(SetFieldCommand(app_state, field, value, text=f"Set {field}"))
    finally:
        stack.endMacro()


def build_session_bundle(app_state: AppState) -> dict:
    """Same shape as core/persistence.py's _build_session_bundle()."""
    d = build_cfg_dict(app_state)

    d["amp_files"] = [
        {"filename": f["filename"], "csv": f["df"].to_csv(index=False), "channels": f["channels"], "cpdf": f["cpdf"].to_dict(orient="records")}
        for f in app_state.data.amp_files
    ]
    d["solid_files"] = [
        {"filename": f["filename"], "csv": f["df"].to_csv(index=False), "channels": f["channels"], "cpdf": f["cpdf"].to_dict(orient="records")}
        for f in app_state.data.solid_files
    ]
    d["cv_runs"] = [
        {"scan_rate": r["scan_rate"], "label": r["label"], "filename": r["filename"], "csv": r["df"].to_csv(index=False), "channels": r["channels"], "peaks": r["peaks"]}
        for r in app_state.data.cv_runs
    ]
    d["assay_plate"] = _plate_df_to_csv(app_state.data.assay_plate)
    d["assay_std_df"] = _jsonify(app_state.data.assay_std_df.to_dict(orient="records"))
    d["assay_sample_df"] = _jsonify(app_state.data.assay_sample_df.to_dict(orient="records"))
    d["assay_std_res"] = _jsonify(app_state.data.assay_std_res)

    return d


def apply_session_bundle(app_state: AppState, d: dict, macro_text: str = "Import session") -> None:
    """Inverse of build_session_bundle — same semantics as
    core/persistence.py's _apply_session_bundle(), pushed as one undo step
    (undo/redo also restores/reapplies the whole import atomically). Every
    view that needs to redraw is already wired to the signals the pushed
    commands fire: FilesListCommand -> files_changed (amp_files/
    solid_files/cv_runs) and SetFieldCommand -> setting_changed (assay_*
    fields, units, smoothing) — no caller-side refresh needed."""
    stack = app_state.undo_stack
    stack.beginMacro(macro_text)
    try:
        apply_cfg_dict(app_state, d, macro_text="Apply settings")

        if "amp_files" in d:
            files = [
                {"filename": f["filename"], "df": pd.read_csv(io.StringIO(f["csv"])), "channels": f["channels"], "cpdf": _cpdf_from_records(f.get("cpdf"))}
                for f in d["amp_files"]
            ]
            stack.push(FilesListCommand(app_state, "amp_files", files, text="Import amp_files"))

        if "solid_files" in d:
            files = [
                {"filename": f["filename"], "df": pd.read_csv(io.StringIO(f["csv"])), "channels": f["channels"], "cpdf": _solid_cpdf_from_records(f.get("cpdf"))}
                for f in d["solid_files"]
            ]
            stack.push(FilesListCommand(app_state, "solid_files", files, text="Import solid_files"))

        if "cv_runs" in d:
            runs = [
                {"scan_rate": float(r["scan_rate"]), "label": r["label"], "filename": r["filename"], "df": pd.read_csv(io.StringIO(r["csv"])), "channels": r["channels"], "peaks": r.get("peaks", {})}
                for r in d["cv_runs"]
            ]
            stack.push(FilesListCommand(app_state, "cv_runs", runs, text="Import cv_runs"))

        if "assay_plate" in d:
            stack.push(SetFieldCommand(app_state, "assay_plate", _plate_df_from_csv(d["assay_plate"]), text="Import assay_plate"))
        if "assay_std_df" in d:
            stack.push(SetFieldCommand(app_state, "assay_std_df", pd.DataFrame(d["assay_std_df"]), text="Import assay_std_df"))
        if "assay_sample_df" in d:
            stack.push(SetFieldCommand(app_state, "assay_sample_df", pd.DataFrame(d["assay_sample_df"]), text="Import assay_sample_df"))
        if "assay_std_res" in d:
            stack.push(SetFieldCommand(app_state, "assay_std_res", d["assay_std_res"], text="Import assay_std_res"))
    finally:
        stack.endMacro()  # each FilesListCommand.redo() already fired files_changed during push()
