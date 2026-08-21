"""Config/session persistence: browser localStorage save + full-session
JSON export/import. Deliberately hardcodes every mode's keys directly
rather than a per-mode-hook abstraction (see refactor plan) — note this
means, same as before the split, solid_unit is never saved and only
amp_files round-trips through Export/Import JSON or Cloud Sessions."""

import io

import pandas as pd
import streamlit as st

from core.calibration_table import _cpdf_from_records

SS = st.session_state


def _apply_cfg_dict(d: dict) -> None:
    """Apply a loaded config dict (from localStorage or an imported JSON file) to session state."""
    if "conc_unit"       in d: SS.conc_unit       = d["conc_unit"]
    if "cur_unit"        in d: SS.cur_unit        = d["cur_unit"]
    if "volt_unit"       in d: SS.volt_unit       = d["volt_unit"]
    if "cv_cur_unit"     in d: SS.cv_cur_unit     = d["cv_cur_unit"]
    if "cv_sr_unit"      in d: SS.cv_sr_unit      = d["cv_sr_unit"]
    if "vol_unit"        in d: SS.vol_unit        = d["vol_unit"]
    if "initial_volume"  in d: SS.initial_volume  = float(d["initial_volume"])
    if "smooth_method"   in d: SS.smooth_method   = d["smooth_method"]
    if "smooth_window"   in d: SS.smooth_window   = int(d["smooth_window"])
    if "smooth_polyorder" in d: SS.smooth_polyorder = int(d["smooth_polyorder"])
    if "assay_sig_unit"  in d: SS.assay_sig_unit  = d["assay_sig_unit"]
    if "assay_conc_unit" in d: SS.assay_conc_unit = d["assay_conc_unit"]
    if "calibration_points" in d:
        # Legacy (pre-per-file-calibration) sessions stored one shared
        # table — keep it as the seed for any newly-imported file rather
        # than discarding it.
        SS["_legacy_cpdf_template"] = _cpdf_from_records(d["calibration_points"])


def _build_cfg_dict() -> dict:
    """Settings only (no raw trace data or calibration tables, which now
    live per-file) — used for the lightweight browser-localStorage save."""
    return {
        "conc_unit":          SS.conc_unit,
        "cur_unit":           SS.cur_unit,
        "volt_unit":          SS.volt_unit,
        "cv_cur_unit":        SS.cv_cur_unit,
        "cv_sr_unit":         SS.cv_sr_unit,
        "vol_unit":           SS.vol_unit,
        "initial_volume":     SS.initial_volume,
        "smooth_method":      SS.smooth_method,
        "smooth_window":      SS.smooth_window,
        "smooth_polyorder":   SS.smooth_polyorder,
        "assay_sig_unit":     SS.assay_sig_unit,
        "assay_conc_unit":    SS.assay_conc_unit,
    }


def _build_session_bundle() -> dict:
    """Full session: settings + the raw amperometry files themselves (as
    embedded CSV text) + each file's own calibration table, so a
    cloud-saved session can be restored on any machine without
    re-uploading the original CSVs or re-filling calibration tables."""
    d = _build_cfg_dict()
    d["amp_files"] = [
        {
            "filename": f["filename"],
            "csv":      f["df"].to_csv(index=False),
            "channels": f["channels"],
            "cpdf":     f["cpdf"].to_dict(orient="records"),
        }
        for f in SS.amp_files
    ]
    return d


def _apply_session_bundle(d: dict) -> None:
    """Inverse of _build_session_bundle — restores settings, the raw
    amperometry files, and each file's own calibration table."""
    _apply_cfg_dict(d)
    if "amp_files" in d:
        _files = []
        for f in d["amp_files"]:
            _files.append({
                "filename": f["filename"],
                "df":       pd.read_csv(io.StringIO(f["csv"])),
                "channels": f["channels"],
                "cpdf":     _cpdf_from_records(f.get("cpdf")),
            })
        SS.amp_files = _files
        SS.df       = _files[0]["df"] if _files else pd.DataFrame()
        SS.channels = _files[0]["channels"] if _files else []
