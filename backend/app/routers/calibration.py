import hashlib
import io
import json
import uuid

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..analysis.calibration import apply_effective_concentration, eff_t_start
from ..analysis.regression import lin_reg, piecewise_fit
from ..analysis.signal import smooth_signal
from ..analysis.solid_state import nernst_ideal_slope_mv, nernstian_lod_fit
from ..auth import get_current_user
from ..db.models import CalibrationResult, Dataset, FitType, InstrumentType, Project, User
from ..db.session import get_db
from ..schemas import CalibrationResultOut, ComputeCalibrationRequest
from .datasets import _get_dataset, _get_project

router = APIRouter(prefix="/projects/{project_id}/calibration", tags=["calibration"])


def _channel_by_name(dataset: Dataset, ch_name: str) -> dict:
    ch = next((c for c in dataset.channel_mappings if c["name"] == ch_name), None)
    if ch is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Channel '{ch_name}' not found in dataset '{dataset.filename}'.",
        )
    return ch


def _compute_amperometry(
    pairs: list[tuple[Dataset, dict]], req: ComputeCalibrationRequest
) -> dict:
    """Windowed-average, baseline-subtracted ΔI per channel — same logic as
    the Streamlit app's `_do_compute_calibration` (app.py), generalized to
    read each dataset's own calibration table instead of a shared SS.cpdf."""
    results: dict = {}
    for dataset, ch in pairs:
        cpdf_full = pd.DataFrame(dataset.calibration_table)
        cpdf_full = apply_effective_concentration(cpdf_full, req.initial_volume)
        cpdf = cpdf_full.dropna(subset=["t_end"]).reset_index(drop=True)
        ch_name = ch["name"]
        label = f"{dataset.filename} · {ch_name}" if len(pairs) > 1 else ch_name
        if cpdf.empty:
            continue

        base_rows = cpdf[cpdf["Baseline"].apply(lambda b: bool(b) if pd.notna(b) else False)]
        base_idx = int(base_rows.index[0]) if len(base_rows) else 0

        df = pd.read_csv(io.BytesIO(dataset.raw_data))
        t_arr = pd.to_numeric(df[ch["tc"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        i_arr = pd.to_numeric(df[ch["ic"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        i_arr = smooth_signal(i_arr, req.smooth_method, req.smooth_window, req.smooth_polyorder)

        avgs, sigs, n_pts, t_starts_used = [], [], [], []
        for _, row in cpdf.iterrows():
            ets = eff_t_start(row)
            t_starts_used.append(ets)
            if ets is None:
                avgs.append(np.nan)
                sigs.append(np.nan)
                n_pts.append(0)
                continue
            mask = (t_arr >= ets) & (t_arr <= row["t_end"])
            pts = i_arr[mask]
            pts = pts[~np.isnan(pts)]
            n_pts.append(int(pts.size))
            avgs.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
            sigs.append(float(np.std(pts, ddof=1)) if pts.size >= 2 else np.nan)

        base_val = avgs[base_idx]
        sigma_bl = sigs[base_idx]
        if np.isnan(base_val):
            continue   # baseline window has no data — can't compute ΔI for this channel
        delta_i = [(v - base_val) if not np.isnan(v) else np.nan for v in avgs]

        results[label] = dict(
            concs=cpdf["Concentration"].astype(float).tolist(),
            labels=cpdf["Label"].tolist(),
            avgs=avgs, sigs=sigs, delta_i=delta_i,
            sigma_bl=float(sigma_bl) if sigma_bl is not None else float("nan"),
            is_average=False, n_pts=n_pts, t_starts_used=t_starts_used,
            t_ends=cpdf["t_end"].tolist(), baselines=cpdf["Baseline"].tolist(),
        )

    if req.show_channel_average and len(results) >= 2:
        chs = list(results.keys())
        all_di = np.array([results[c]["delta_i"] for c in chs], dtype=float)
        all_avgs = np.array([results[c]["avgs"] for c in chs], dtype=float)
        all_sigma = [results[c]["sigma_bl"] for c in chs]
        valid_s = [s for s in all_sigma if np.isfinite(s)]
        sigma_bl_avg = (np.sqrt(sum(s ** 2 for s in valid_s)) / len(chs)) if valid_s else np.nan
        results["Channel Average"] = dict(
            concs=results[chs[0]]["concs"], labels=results[chs[0]]["labels"],
            avgs=np.nanmean(all_avgs, axis=0).tolist(),
            sigs=np.nanstd(all_di, axis=0, ddof=1).tolist(),
            delta_i=np.nanmean(all_di, axis=0).tolist(),
            sigma_bl=float(sigma_bl_avg), is_average=True,
            baselines=results[chs[0]]["baselines"],
        )
    return results


def _compute_solid_state(pairs: list[tuple[Dataset, dict]], req: ComputeCalibrationRequest) -> dict:
    """Nernstian (E vs log10 concentration) fit per channel — see
    backend/app/analysis/solid_state.py for why this is NOT the same as
    Amperometry's ΔI model (no baseline subtraction, LOD via independent-
    segment intersection rather than 3·sigma_blank/slope)."""
    results: dict = {}
    for dataset, ch in pairs:
        cpdf = pd.DataFrame(dataset.calibration_table)
        ch_name = ch["name"]
        label = f"{dataset.filename} · {ch_name}" if len(pairs) > 1 else ch_name

        rejected = cpdf["Concentration"].astype(float) <= 0
        if rejected.any():
            cpdf = cpdf[~rejected].reset_index(drop=True)
        if cpdf.empty:
            continue

        readings = []
        if "Reading_mV" in cpdf.columns:
            df_trace = None
            for _, row in cpdf.iterrows():
                if pd.notna(row.get("Reading_mV")):
                    readings.append(float(row["Reading_mV"]))
                    continue
                if df_trace is None:
                    df_trace = pd.read_csv(io.BytesIO(dataset.raw_data))
                t_arr = pd.to_numeric(df_trace[ch["tc"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                e_arr = pd.to_numeric(df_trace[ch["ic"]], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
                ets = eff_t_start(row)
                if ets is None or pd.isna(row.get("t_end")):
                    readings.append(np.nan)
                    continue
                mask = (t_arr >= ets) & (t_arr <= row["t_end"])
                pts = e_arr[mask]
                pts = pts[~np.isnan(pts)]
                readings.append(float(np.mean(pts)) if pts.size > 0 else np.nan)
        else:
            readings = [np.nan] * len(cpdf)

        log_conc = np.log10(cpdf["Concentration"].astype(float).to_numpy())
        potential = np.array(readings, dtype=float)
        valid = ~np.isnan(potential)
        if valid.sum() < 2:
            continue

        lod_fit = nernstian_lod_fit(log_conc[valid], potential[valid])
        continuous_fit = piecewise_fit(log_conc[valid], potential[valid], 2)
        nernst_seg = lod_fit["nernstian_segment"]
        ideal = nernst_ideal_slope_mv()

        results[label] = dict(
            concs=cpdf["Concentration"].astype(float).tolist(),
            labels=cpdf["Label"].tolist(),
            log_conc=log_conc.tolist(),
            potential_mv=potential.tolist(),
            low_segment=lod_fit["low_segment"],
            nernstian_segment=nernst_seg,
            continuous_fit=continuous_fit,
            lod_log10=lod_fit["lod_log10"],
            lod_conc=lod_fit["lod_conc"],
            sensitivity_mv_per_decade=nernst_seg["slope"] if nernst_seg else None,
            pct_of_ideal_nernstian=(
                100.0 * abs(nernst_seg["slope"]) / ideal if nernst_seg else None
            ),
            ideal_slope_mv_per_decade=ideal,
            is_average=False,
        )
    return results


@router.post("/compute", response_model=CalibrationResultOut)
def compute_calibration(
    project_id: uuid.UUID,
    req: ComputeCalibrationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalibrationResult:
    project = _get_project(db, project_id)
    pairs = []
    for dataset_id, ch_name in req.dataset_channel_pairs:
        dataset = _get_dataset(db, project_id, dataset_id)
        pairs.append((dataset, _channel_by_name(dataset, ch_name)))
    if not pairs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Select at least one channel to analyse.")

    if project.instrument_type == InstrumentType.solid_state:
        results = _compute_solid_state(pairs, req)
        fit_type = FitType.nernstian
        n_segments = None
    else:
        results = _compute_amperometry(pairs, req)
        fit_type = FitType.segmented_linear if req.fit_type == "segmented_linear" else FitType.linear
        n_segments = req.n_segments if fit_type == FitType.segmented_linear else None

    if not results:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No channel produced valid results — check the calibration table(s).",
        )

    input_hash = hashlib.sha256(
        json.dumps(
            {"pairs": [str(p[0].id) + p[1]["name"] for p in pairs],
             "tables": [p[0].calibration_table for p in pairs],
             "req": req.model_dump(mode="json")},
            sort_keys=True, default=str,
        ).encode()
    ).hexdigest()

    result = CalibrationResult(
        project_id=project.id,
        fit_type=fit_type,
        n_segments=n_segments,
        results=results,
        contributing_dataset_ids=[str(d.id) for d, _ in pairs],
        input_hash=input_hash,
        computed_by=user.id,
    )
    db.add(result)
    db.commit()
    db.refresh(result)
    return result


@router.get("/{result_id}", response_model=CalibrationResultOut)
def get_calibration_result(
    project_id: uuid.UUID, result_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> CalibrationResult:
    result = db.get(CalibrationResult, result_id)
    if result is None or result.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calibration result not found.")
    return result
