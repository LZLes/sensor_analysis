"""
End-to-end integration test against the real (local) Postgres instance:
create a project, upload the bundled sample data, configure a channel +
calibration table, and compute a calibration result — mirroring the exact
workflow verified by hand in the Streamlit app this session.

Auth is bypassed via FastAPI's dependency_overrides (the standard pattern
for testing routes behind auth) rather than a real Google OAuth round trip.
"""
import io
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db.models import Project, User
from app.db.session import SessionLocal
from app.main import app

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db_session):
    email = f"test-{uuid.uuid4()}@example.com"
    user = db_session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name="Test User", is_admin=True)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    yield user
    db_session.delete(db_session.get(User, user.id))
    db_session.commit()


@pytest.fixture
def client(test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_full_amperometry_workflow(client, db_session):
    # 1. Create an amperometry project
    r = client.post("/projects", json={"name": "Test run", "instrument_type": "amperometry"})
    assert r.status_code == 200, r.text
    project = r.json()
    project_id = project["id"]

    # 2. Upload the bundled sample CSV as a standard (single-header-row) file
    with open(os.path.join(SAMPLE_DIR, "sensor_run_A.csv"), "rb") as f:
        r = client.post(
            f"/projects/{project_id}/datasets",
            files={"file": ("sensor_run_A.csv", f, "text/csv")},
            data={"file_format": "standard", "delimiter": ","},
        )
    assert r.status_code == 200, r.text
    dataset = r.json()
    dataset_id = dataset["id"]
    # Standard-CSV upload doesn't auto-detect channels — set them explicitly,
    # same as the "Map Columns to Channels" step in the Streamlit app.
    assert dataset["channel_mappings"] == []

    r = client.patch(
        f"/projects/{project_id}/datasets/{dataset_id}/channel-mappings",
        json=[
            {"name": "Channel A", "tc": "Time (s)", "ic": "Channel A (uA)"},
            {"name": "Channel B", "tc": "Time (s)", "ic": "Channel B (uA)"},
        ],
    )
    assert r.status_code == 200, r.text

    # 3. Set the calibration table matching the sample data's known ground truth
    calibration_table = [
        {"Label": "Blank", "Concentration": 0.0, "Spike Vol": None, "Stock Conc": None,
         "t_start": 0.0, "t_end": 50.0, "avg_duration": None, "Baseline": True},
        {"Label": "Step 1", "Concentration": 0.1, "Spike Vol": None, "Stock Conc": None,
         "t_start": 70.0, "t_end": 110.0, "avg_duration": None, "Baseline": False},
        {"Label": "Step 2", "Concentration": 0.5, "Spike Vol": None, "Stock Conc": None,
         "t_start": 130.0, "t_end": 170.0, "avg_duration": None, "Baseline": False},
        {"Label": "Step 3", "Concentration": 1.0, "Spike Vol": None, "Stock Conc": None,
         "t_start": 190.0, "t_end": 230.0, "avg_duration": None, "Baseline": False},
        {"Label": "Step 4", "Concentration": 2.0, "Spike Vol": None, "Stock Conc": None,
         "t_start": 250.0, "t_end": 290.0, "avg_duration": None, "Baseline": False},
    ]
    r = client.patch(
        f"/projects/{project_id}/datasets/{dataset_id}/calibration-table",
        json={"calibration_table": calibration_table},
    )
    assert r.status_code == 200, r.text

    # 4. Compute calibration for Channel A
    r = client.post(
        f"/projects/{project_id}/calibration/compute",
        json={
            "dataset_channel_pairs": [[dataset_id, "Channel A"]],
            "fit_type": "linear",
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()
    ch_result = result["results"]["Channel A"]
    # Known ground truth (verified by hand against the Streamlit app earlier
    # this session, and against backend/tests re-derivation): baseline ~1.0
    # uA, ΔI increasing with concentration, R^2 close to 1 for this
    # synthetic step-response data.
    assert ch_result["delta_i"][0] == pytest.approx(0.0, abs=1e-6)   # blank is always 0 by construction
    assert ch_result["avgs"][0] == pytest.approx(1.0, abs=0.05)
    assert all(
        ch_result["delta_i"][i] < ch_result["delta_i"][i + 1]
        for i in range(len(ch_result["delta_i"]) - 1)
    ), "delta_i should increase monotonically with concentration for this dataset"

    # 5. Export as CSV works end-to-end too
    r = client.get(
        f"/projects/{project_id}/export/calibration-summary.csv",
        params={"result_id": result["id"]},
    )
    assert r.status_code == 200
    assert b"Channel" in r.content

    # Cleanup
    client.delete(f"/projects/{project_id}")


def test_unauthenticated_request_rejected():
    # No dependency override here -> real get_current_user runs -> 401
    with TestClient(app) as c:
        r = c.get("/projects")
    assert r.status_code == 401


def test_solid_state_workflow_rejects_zero_concentration_and_computes_nernstian_fit(client):
    r = client.post("/projects", json={"name": "ISE test", "instrument_type": "solid_state"})
    assert r.status_code == 200
    project_id = r.json()["id"]

    # Synthetic potentiometric trace: two channels aren't needed — one is enough.
    csv_bytes = b"Time (s),Potential (mV)\n" + b"\n".join(
        f"{t},{100.0 + 0.1 * t}".encode() for t in range(0, 60)
    )
    r = client.post(
        f"/projects/{project_id}/datasets",
        files={"file": ("ise_run.csv", io.BytesIO(csv_bytes), "text/csv")},
        data={"file_format": "standard", "delimiter": ","},
    )
    assert r.status_code == 200
    dataset_id = r.json()["id"]
    client.patch(
        f"/projects/{project_id}/datasets/{dataset_id}/channel-mappings",
        json=[{"name": "ISE", "tc": "Time (s)", "ic": "Potential (mV)"}],
    )

    # Direct-entry readings (Reading_mV filled) for a clean Nernstian series,
    # including one Concentration <= 0 row that must be rejected, not crash.
    calibration_table = [
        {"Label": "Invalid", "Concentration": 0.0, "t_start": None, "t_end": None,
         "avg_duration": None, "Reading_mV": 500.0},
        {"Label": "1e-5 M", "Concentration": 1e-5, "t_start": None, "t_end": None,
         "avg_duration": None, "Reading_mV": 100.0},
        {"Label": "1e-4 M", "Concentration": 1e-4, "t_start": None, "t_end": None,
         "avg_duration": None, "Reading_mV": 159.16},
        {"Label": "1e-3 M", "Concentration": 1e-3, "t_start": None, "t_end": None,
         "avg_duration": None, "Reading_mV": 218.32},
        {"Label": "1e-2 M", "Concentration": 1e-2, "t_start": None, "t_end": None,
         "avg_duration": None, "Reading_mV": 277.48},
    ]
    client.patch(
        f"/projects/{project_id}/datasets/{dataset_id}/calibration-table",
        json={"calibration_table": calibration_table},
    )

    r = client.post(
        f"/projects/{project_id}/calibration/compute",
        json={"dataset_channel_pairs": [[dataset_id, "ISE"]]},
    )
    assert r.status_code == 200, r.text
    ch_result = r.json()["results"]["ISE"]
    # The "Invalid" (Concentration<=0) row must be excluded, not crash the fit.
    assert 0.0 not in ch_result["concs"]
    assert ch_result["sensitivity_mv_per_decade"] == pytest.approx(59.16, abs=1.0)
    assert ch_result["pct_of_ideal_nernstian"] == pytest.approx(100.0, abs=3.0)

    client.delete(f"/projects/{project_id}")
