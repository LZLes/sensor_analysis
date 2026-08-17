import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..db.models import InstrumentType, Project, User
from ..db.session import get_db
from ..schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_owned_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return project


@router.post("", response_model=ProjectOut)
def create_project(
    body: ProjectCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Project:
    try:
        instrument_type = InstrumentType(body.instrument_type)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown instrument_type: {body.instrument_type}")
    project = Project(name=body.name, owner_id=user.id, instrument_type=instrument_type, notes=body.notes)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Project]:
    # Internal-tool scale: everyone on the allowlist sees every project —
    # no per-account data isolation, matching the shared-team decision.
    return list(db.scalars(select(Project).order_by(Project.updated_at.desc())))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Project:
    return _get_owned_project(db, project_id)


@router.delete("/{project_id}")
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    project = _get_owned_project(db, project_id)
    db.delete(project)
    db.commit()
    return {"ok": True}
