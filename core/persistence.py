"""Config/session persistence: browser localStorage save + full-session
JSON export/import. Deliberately hardcodes every mode's keys directly
rather than a per-mode-hook abstraction (see refactor plan) — each mode's
raw data (amp_files, solid_files, cv_runs, assay_*) round-trips through
_build_session_bundle/_apply_session_bundle (Export/Import JSON and Cloud
Sessions); only lightweight settings (units, not raw data) go through
_build_cfg_dict/_apply_cfg_dict (the browser-localStorage Save, which stays
well under storage quotas by design)."""

import io

import numpy as np
import pandas as pd
import streamlit as st

from core.calibration_table import _cpdf_from_records, _solid_cpdf_from_records

SS = st.session_state


def _jsonify(obj):
    """Recursively convert numpy scalar types to native Python. Without
    this, a numpy.float64 buried in a dict (e.g. assay_std_res's 4PL fit
    params, which come straight out of scipy.optimize.curve_fit) survives
    into json.dumps(..., default=str) as a STRING instead of a number —
    default=str stringifies anything it can't serialize natively — silently
    corrupting the round-trip. Leaves plain dict/list/str/float/int/bool/
    None untouched."""
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _apply_cfg_dict(d: dict) -> None:
    """Apply a loaded config dict (from localStorage or an imported JSON file) to session state."""
    if "conc_unit"       in d: SS.conc_unit       = d["conc_unit"]
    if "solid_conc_unit" in d: SS.solid_conc_unit = d["solid_conc_unit"]
    if "cur_unit"        in d: SS.cur_unit        = d["cur_unit"]
    if "solid_unit"      in d: SS.solid_unit      = d["solid_unit"]
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
        "solid_conc_unit":    SS.solid_conc_unit,
        "cur_unit":           SS.cur_unit,
        "solid_unit":         SS.solid_unit,
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


def _plate_df_to_csv(plate_df: pd.DataFrame | None) -> str | None:
    return plate_df.to_csv() if plate_df is not None else None


def _plate_df_from_csv(csv_text: str | None) -> pd.DataFrame | None:
    if not csv_text:
        return None
    _df = pd.read_csv(io.StringIO(csv_text), index_col=0)
    _df.columns = pd.Index([int(c) for c in _df.columns], name="Col")
    _df.index.name = "Row"
    return _df


def _build_session_bundle() -> dict:
    """Full session: settings + every mode's raw data + calibration/standard
    tables, so a cloud-saved session can be restored on any machine without
    re-uploading files or re-filling tables. Each mode's block below mirrors
    the amp_files pattern (df -> CSV text, small dicts/records as-is)."""
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

    d["solid_files"] = [
        {
            "filename": f["filename"],
            "csv":      f["df"].to_csv(index=False),
            "channels": f["channels"],
            "cpdf":     f["cpdf"].to_dict(orient="records"),
        }
        for f in SS.solid_files
    ]

    d["cv_runs"] = [
        {
            "scan_rate": r["scan_rate"],
            "label":     r["label"],
            "filename":  r["filename"],
            "csv":       r["df"].to_csv(index=False),
            "channels":  r["channels"],
            "peaks":     r["peaks"],
        }
        for r in SS.cv_runs
    ]

    d["assay_plate"]      = _plate_df_to_csv(SS.assay_plate)
    d["assay_std_df"]     = _jsonify(SS.assay_std_df.to_dict(orient="records"))
    d["assay_sample_df"]  = _jsonify(SS.assay_sample_df.to_dict(orient="records"))
    d["assay_std_res"]    = _jsonify(SS.assay_std_res)

    return d


def _apply_session_bundle(d: dict) -> None:
    """Inverse of _build_session_bundle — restores settings and every
    mode's raw data / calibration / standard tables."""
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

    if "solid_files" in d:
        SS.solid_files = [
            {
                "filename": f["filename"],
                "df":       pd.read_csv(io.StringIO(f["csv"])),
                "channels": f["channels"],
                "cpdf":     _solid_cpdf_from_records(f.get("cpdf")),
            }
            for f in d["solid_files"]
        ]

    if "cv_runs" in d:
        SS.cv_runs = [
            {
                "scan_rate": float(r["scan_rate"]),
                "label":     r["label"],
                "filename":  r["filename"],
                "df":        pd.read_csv(io.StringIO(r["csv"])),
                "channels":  r["channels"],
                "peaks":     r.get("peaks", {}),
            }
            for r in d["cv_runs"]
        ]

    if "assay_plate" in d:
        SS.assay_plate = _plate_df_from_csv(d["assay_plate"])
    if "assay_std_df" in d:
        SS.assay_std_df = pd.DataFrame(d["assay_std_df"])
    if "assay_sample_df" in d:
        SS.assay_sample_df = pd.DataFrame(d["assay_sample_df"])
    if "assay_std_res" in d:
        SS.assay_std_res = d["assay_std_res"]
