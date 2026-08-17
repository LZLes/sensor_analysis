"""Pydantic request/response models for the API."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GoogleLoginRequest(BaseModel):
    id_token: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    is_admin: bool


class ProjectCreate(BaseModel):
    name: str
    instrument_type: str
    notes: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    instrument_type: str
    notes: str | None
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    channel_mappings: list
    calibration_table: list
    order_index: int
    uploaded_at: datetime


class CalibrationTableUpdate(BaseModel):
    calibration_table: list[dict]


class ComputeCalibrationRequest(BaseModel):
    dataset_channel_pairs: list[tuple[uuid.UUID, str]]   # (dataset_id, channel_name)
    fit_type: str = "linear"          # "linear" | "segmented_linear" | "nernstian"
    n_segments: int = 1
    show_channel_average: bool = False
    smooth_method: str = "None"
    smooth_window: int = 11
    smooth_polyorder: int = 2
    initial_volume: float = 1.0


class CalibrationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    fit_type: str
    n_segments: int | None
    results: dict
    contributing_dataset_ids: list
    computed_at: datetime
    insights_text: str | None = None


class InsightsRequest(BaseModel):
    calibration_result_id: uuid.UUID
