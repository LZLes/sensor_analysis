"""
SQLAlchemy models for the approved schema (see /root/.claude/plans/
i-want-to-add-linked-cocoa.md, "Relational schema" section).

- `users` rows ARE the access allowlist — no self-signup.
- `projects.instrument_type` picks which of the four shells a project uses;
  one project = one instrument type (matches today's single active "mode").
- `datasets` / `calibration_results` are shared by Amperometry AND
  Solid-State (same per-file shape); CV and Assay get their own parallel
  pairs because their underlying shapes are genuinely different.
- Calibration tables and result blobs are JSONB, not normalized child
  tables — small, always read/written as a whole unit.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .session import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class InstrumentType(str, enum.Enum):
    amperometry = "amperometry"
    cv = "cv"
    assay = "assay"
    solid_state = "solid_state"


class FitType(str, enum.Enum):
    linear = "linear"
    segmented_linear = "segmented_linear"
    nernstian = "nernstian"


class AssayFitType(str, enum.Enum):
    linear = "linear"
    quadratic = "quadratic"
    four_pl = "4pl"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="owner")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    instrument_type: Mapped[InstrumentType] = mapped_column(Enum(InstrumentType), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    owner: Mapped[User] = relationship(back_populates="projects")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    calibration_results: Mapped[list["CalibrationResult"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    cv_runs: Mapped[list["CVRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    cv_results: Mapped[list["CVResult"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assay_plates: Mapped[list["AssayPlate"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assay_results: Mapped[list["AssayResult"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Dataset(Base):
    """One imported file — Amperometry AND Solid-State (same shape: raw
    file + channel mappings + its own calibration table, matching today's
    per-file amp_files[i])."""
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    raw_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    channel_mappings: Mapped[list] = mapped_column(JSONB, default=list)      # [{name, tc, ic}, ...]
    calibration_table: Mapped[list] = mapped_column(JSONB, default=list)     # schema differs by instrument_type
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="datasets")


class CalibrationResult(Base):
    """One row per 'Compute Calibration' click. Scoped at the PROJECT level,
    not per-dataset, because the 'Channel Average' feature already combines
    channels from different datasets — see the plan doc for why."""
    __tablename__ = "calibration_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    fit_type: Mapped[FitType] = mapped_column(Enum(FitType), nullable=False)
    n_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    results: Mapped[dict] = mapped_column(JSONB, nullable=False)            # {ch_name: {...}}
    contributing_dataset_ids: Mapped[list] = mapped_column(JSONB, default=list)
    input_hash: Mapped[str] = mapped_column(String, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    computed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # AI Insights (added mid-build) — cached alongside the result it describes.
    insights_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    insights_model: Mapped[str | None] = mapped_column(String, nullable=True)
    insights_generated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="calibration_results")


class CVRun(Base):
    __tablename__ = "cv_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    scan_rate: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    raw_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    channel_mappings: Mapped[list] = mapped_column(JSONB, default=list)
    detected_peaks: Mapped[dict] = mapped_column(JSONB, default=dict)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="cv_runs")


class CVResult(Base):
    __tablename__ = "cv_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False)   # Ip-v, Randles-Sevcik fit, Ep-v
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    computed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    project: Mapped[Project] = relationship(back_populates="cv_results")


class AssayPlate(Base):
    __tablename__ = "assay_plates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    raw_plate_data: Mapped[dict] = mapped_column(JSONB, nullable=False)      # 8x12 grid
    standards_table: Mapped[list] = mapped_column(JSONB, default=list)       # Label/Conc/S1/S2/S3
    sample_table: Mapped[list] = mapped_column(JSONB, default=list)          # Well/Label pairs
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="assay_plates")


class AssayResult(Base):
    __tablename__ = "assay_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    fit_type: Mapped[AssayFitType] = mapped_column(Enum(AssayFitType), nullable=False)
    fit_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    back_calculated: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    computed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    project: Mapped[Project] = relationship(back_populates="assay_results")
