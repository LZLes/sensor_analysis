import io
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.rendering import MIME, render_cal_png, render_ts_png
from ..auth import get_current_user
from ..db.models import CalibrationResult, Dataset, User
from ..db.session import get_db
from .datasets import _get_project

router = APIRouter(prefix="/projects/{project_id}/export", tags=["export"])


@router.get("/time-series")
def export_time_series_png(
    project_id: uuid.UUID,
    fmt: str = "png",
    dpi: int = 150,
    style: str = "default",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    _get_project(db, project_id)
    datasets = list(db.scalars(select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.order_index)))
    if not datasets:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No datasets in this project yet.")

    amp_files = [
        {
            "filename": d.filename,
            "df": pd.read_csv(io.BytesIO(d.raw_data)),
            "channels": d.channel_mappings,
            "cpdf": pd.DataFrame(d.calibration_table),
        }
        for d in datasets
    ]
    visible = [c["name"] for d in datasets for c in d.channel_mappings]
    png = render_ts_png(amp_files, "µA", visible, dpi=dpi, fmt=fmt, style=style)
    return Response(content=png, media_type=MIME.get(fmt, "image/png"))


@router.get("/calibration-curve/{result_id}")
def export_calibration_curve_png(
    project_id: uuid.UUID,
    result_id: uuid.UUID,
    fmt: str = "png",
    dpi: int = 150,
    style: str = "default",
    conc_unit: str = "mM",
    cur_unit: str = "µA",
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    result = db.get(CalibrationResult, result_id)
    if result is None or result.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calibration result not found.")
    png = render_cal_png(
        result.results, "Segmented Linear" if result.n_segments else "Linear",
        result.n_segments or 1, conc_unit, cur_unit, dpi=dpi, fmt=fmt, style=style,
    )
    return Response(content=png, media_type=MIME.get(fmt, "image/png"))


@router.get("/calibration-summary.csv")
def export_calibration_summary_csv(
    project_id: uuid.UUID,
    result_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    result = db.get(CalibrationResult, result_id)
    if result is None or result.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Calibration result not found.")
    rows = []
    for ch_name, res in result.results.items():
        for i, label in enumerate(res.get("labels", [])):
            rows.append({
                "Channel": ch_name, "Label": label,
                "Concentration": res["concs"][i] if i < len(res.get("concs", [])) else None,
                "ΔI": res["delta_i"][i] if i < len(res.get("delta_i", [])) else None,
            })
    csv_bytes = pd.DataFrame(rows).to_csv(index=False).encode()
    return Response(content=csv_bytes, media_type="text/csv")
