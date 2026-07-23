import io
import uuid

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis.defaults import default_calibration_table
from ..analysis.parsing import parse_potentiostat_csv, parse_pssession
from ..auth import get_current_user
from ..db.models import Dataset, InstrumentType, Project, User
from ..db.session import get_db
from ..schemas import CalibrationTableUpdate, DatasetOut

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def _get_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return project


def _get_dataset(db: Session, project_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    return dataset


@router.post("", response_model=DatasetOut)
def upload_dataset(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    file_format: str = "standard",   # "standard" | "multichannel"
    delimiter: str = ",",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dataset:
    project = _get_project(db, project_id)
    raw_bytes = file.file.read()

    try:
        if file.filename.lower().endswith(".pssession"):
            df, channels = parse_pssession(raw_bytes)
        else:
            raw_text = raw_bytes.decode("utf-8", errors="replace")
            if file_format == "multichannel":
                mode = "cv" if project.instrument_type == InstrumentType.cv else "amperometry"
                df, channels = parse_potentiostat_csv(raw_text, delimiter, mode=mode)
            else:
                df = pd.read_csv(io.StringIO(raw_text), sep=delimiter)
                channels = []
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    order_index = len(list(db.scalars(select(Dataset).where(Dataset.project_id == project.id))))

    # Store the PARSED, tidy dataframe (not the original upload bytes) so
    # every downstream read (compute, export) is a plain pd.read_csv with
    # no need to remember which format/delimiter/mode this file used.
    dataset = Dataset(
        project_id=project.id,
        filename=file.filename,
        raw_data=df.to_csv(index=False).encode("utf-8"),
        channel_mappings=channels,
        calibration_table=default_calibration_table(project.instrument_type.value),
        uploaded_by=user.id,
        order_index=order_index,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("", response_model=list[DatasetOut])
def list_datasets(
    project_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[Dataset]:
    _get_project(db, project_id)
    return list(
        db.scalars(
            select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.order_index)
        )
    )


@router.patch("/{dataset_id}/calibration-table", response_model=DatasetOut)
def update_calibration_table(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: CalibrationTableUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dataset:
    dataset = _get_dataset(db, project_id, dataset_id)
    dataset.calibration_table = body.calibration_table
    db.commit()
    db.refresh(dataset)
    return dataset


@router.patch("/{dataset_id}/channel-mappings", response_model=DatasetOut)
def update_channel_mappings(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    channel_mappings: list[dict],
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dataset:
    dataset = _get_dataset(db, project_id, dataset_id)
    dataset.channel_mappings = channel_mappings
    db.commit()
    db.refresh(dataset)
    return dataset


@router.delete("/{dataset_id}")
def delete_dataset(
    project_id: uuid.UUID, dataset_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> dict:
    dataset = _get_dataset(db, project_id, dataset_id)
    db.delete(dataset)
    db.commit()
    return {"ok": True}
