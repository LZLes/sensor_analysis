"""HTTP-level tests for the local web app (web_app/), driven through
FastAPI's TestClient against the real core/ and modes/ code."""
import io
import os
import json

import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from web_app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.get("/api/session/ping")
        yield c


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- session plumbing -----------------------------------------------------------
def test_unknown_cookie_id_is_reused_not_replaced():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.get("/api/assay/state", headers={"Cookie": "sensor_session=" + "a" * 32})
        assert ("sensor_session=" + "a" * 32) in r.headers["set-cookie"]


def test_index_and_plotly_served(client):
    assert "Sensor Calibration Studio" in client.get("/").text
    assert client.get("/js/plotly.min.js").status_code == 200


# -- Amperometry / Solid-State ------------------------------------------------------
def test_amperometry_sample_compute_and_exports(client):
    state = _ok(client.post("/api/amperometry/files/sample"))
    assert len(state["files"]) == 2
    res = _ok(client.post("/api/amperometry/compute", json={"selected": state["channel_labels"][:2]}))
    assert res["figure"] and res["stats"]
    r = client.post("/api/amperometry/export/curve", json={"fmt": "svg", "dpi": 96})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")
    assert client.post("/api/amperometry/export/curve", json={"fmt": "bmp"}).status_code == 422


def test_table_cells_with_text_are_coerced_not_500(client):
    _ok(client.post("/api/amperometry/files/sample"))
    rows = [{"Label": "Baseline", "Concentration": "0", "t_start": 0, "t_end": 50, "Baseline": True},
            {"Label": "S1", "Concentration": "oops", "t_start": 60, "t_end": 100, "Baseline": None}]
    state = _ok(client.post("/api/amperometry/files/0/table", json={"rows": rows}))
    assert state["files"][0]["cpdf"][1]["Concentration"] is None
    assert state["files"][0]["cpdf"][1]["Baseline"] is False
    labels = state["channel_labels"]
    assert client.post("/api/amperometry/compute", json={"selected": labels[:1]}).status_code == 200


def test_channel_assignment_rejects_missing_columns(client):
    _ok(client.post("/api/solid_state/files/sample"))
    r = client.post("/api/solid_state/files/0/channels",
                    json={"channels": [{"name": "X", "tc": "nope", "ic": "nope"}]})
    assert r.status_code == 400


def test_remove_and_clear_files(client):
    state = _ok(client.post("/api/amperometry/files/sample"))
    state = _ok(client.delete("/api/amperometry/files/0"))
    assert len(state["files"]) == 1
    assert _ok(client.delete("/api/amperometry/files"))["files"] == []


def test_solid_state_csv_includes_potentials(client):
    state = _ok(client.post("/api/solid_state/files/sample"))
    _ok(client.post("/api/solid_state/compute", json={"selected": state["channel_labels"][:1]}))
    text = client.get("/api/solid_state/export/csv").text
    header = text.splitlines()[0]
    assert "Potential" in header and "log10" in header


# -- Cyclic Voltammetry -----------------------------------------------------------------
def _cv_csv(scale=1.0):
    v = np.concatenate([np.linspace(-0.2, 0.6, 100), np.linspace(0.6, -0.2, 100)])
    i = scale * np.exp(-((v - 0.25) / 0.05) ** 2) * np.r_[np.ones(100), -np.ones(100)]
    return "V,I1,I2\n" + "\n".join(f"{a},{b},{b * 1.1}" for a, b in zip(v, i))


def test_cv_reupload_replaces_and_averaged_channel_survives_reassignment(client):
    files = [("files", ("run_50.csv", _cv_csv(), "text/csv"))]
    _ok(client.post("/api/cv/files", files=files))
    state = _ok(client.post("/api/cv/files", files=files))
    assert len(state["runs"]) == 1  # re-upload replaces, doesn't duplicate

    state = _ok(client.post("/api/cv/runs/0/channels",
                            json={"channels": [{"name": "avg", "vc": "V", "ic_cols": ["I1", "I2"]}]}))
    cols = _ok(client.get("/api/cv/runs/0/columns"))
    assert cols["channels"][0]["ic_cols"] == ["I1", "I2"]
    assert not any(c.startswith("__avg_") for c in cols["columns"])
    # re-applying the mapping as shown must keep the averaged channel
    again = _ok(client.post("/api/cv/runs/0/channels", json={"channels": cols["channels"]}))
    assert [c["name"] for c in again["runs"][0]["channels"]] == ["avg"]


def test_cv_scan_rate_edit_resorts_and_unit_relabels(client):
    _ok(client.post("/api/cv/files", files=[("files", ("a_10.csv", _cv_csv(), "text/csv")),
                                            ("files", ("b_100.csv", _cv_csv(2), "text/csv"))]))
    state = _ok(client.post("/api/cv/runs/0/scan-rate", json={"scan_rate": 500}))
    assert [r["scan_rate"] for r in state["runs"]] == [100, 500]
    assert client.post("/api/cv/runs/0/scan-rate", json={"scan_rate": -1}).status_code == 400
    state = _ok(client.post("/api/cv/units", json={"sr_unit": "V/s"}))
    assert state["scan_rate_labels"] == ["100 V/s", "500 V/s"]


# -- Assay ------------------------------------------------------------------------------
def test_assay_full_flow(client):
    state = _ok(client.post("/api/assay/plate/sample"))
    assert state["n_wells"] > 80 and state["plate_figure"]
    for fit in ["Linear", "Quadratic", "4-Parameter Logistic (4PL)"]:
        res = _ok(client.post("/api/assay/compute", json={"fit_type": fit}))
        assert res["figure"] and len(res["standards"]) == 8
    results = _ok(client.get("/api/assay/results"))
    assert len(results["rows"]) > 50
    assert all(r["Well"][0] in "ABCDEFGH" for r in results["rows"])
    assert client.get("/api/assay/export/results-csv").text.startswith("Well,Label")
    assert "Fit:" in client.get("/api/assay/export/standards-csv").text
    r = client.post("/api/assay/export/curve", json={"fmt": "png", "dpi": 72})
    assert r.content[:4] == b"\x89PNG"


def test_assay_4pl_recovers_sample_concentrations(client):
    """The sample plate is 0.05 + 2c/(c+15); back-calc should invert it."""
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/assay/compute", json={"fit_type": "4-Parameter Logistic (4PL)"}))
    std = client.get("/api/assay/curve").json()["standards"]
    back = [float(r["Back-calc conc"]) for r in std[1:]]
    true = [1, 2, 5, 10, 20, 50, 100]
    for b, t in zip(back, true):
        assert b == pytest.approx(t, rel=0.25)


def test_assay_errors_are_400s(client):
    assert client.post("/api/assay/compute", json={"fit_type": "Linear"}).status_code == 400
    _ok(client.post("/api/assay/plate/sample"))
    rows = _ok(client.get("/api/assay/state"))["std_rows"]
    rows[0]["Conc"] = None
    _ok(client.post("/api/assay/layout", json={"std_rows": rows, "sample_rows": []}))
    r = client.post("/api/assay/compute", json={"fit_type": "Linear"})
    assert r.status_code == 400 and "Blank" in r.json()["detail"]
    bad = client.post("/api/assay/plate/upload", files={"file": ("x.csv", "nothing here", "text/csv")})
    assert bad.status_code == 400


def test_assay_layout_flags_duplicate_and_invalid_wells(client):
    _ok(client.post("/api/assay/plate/sample"))
    rows = _ok(client.get("/api/assay/state"))["std_rows"]
    rows[1]["S1"] = "a1"      # duplicate of the blank's A1 (case-insensitive)
    rows[2]["S2"] = "Z99"
    state = _ok(client.post("/api/assay/layout",
                            json={"std_rows": rows, "sample_rows": [{"Well": "d1", "Label": "P1"}]}))
    probs = " ".join(state["layout_problems"])
    assert "A1 is used twice" in probs and "Z99" in probs
    assert state["sample_rows"] == [{"Well": "D1", "Label": "P1"}]


def test_assay_manual_grid_edit(client):
    grid = [[None] * 12 for _ in range(8)]
    grid[0][0] = 1.5
    state = _ok(client.post("/api/assay/plate", json={"grid": grid}))
    assert state["plate"][0][0] == 1.5 and state["n_wells"] == 1
    assert client.post("/api/assay/plate", json={"grid": [[1]]}).status_code == 400


# -- Session export/import -------------------------------------------------------------
def test_session_roundtrip_includes_assay(client):
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/assay/units", json={"sig_unit": "RFU"}))
    _ok(client.post("/api/assay/compute", json={"fit_type": "Linear"}))
    _ok(client.post("/api/solid_state/files/sample"))
    bundle = client.get("/api/session/export").content
    d = json.loads(bundle)
    assert d["assay_plate"] and d["assay_std_res"]["fit"]["type"] == "linear"

    with TestClient(app, base_url="http://127.0.0.1") as other:
        res = _ok(other.post("/api/session/import", files={"file": ("s.json", bundle, "application/json")}))
        assert res["assay_plate"] and res["solid_files"] == 1
        st = _ok(other.get("/api/assay/state"))
        assert st["sig_unit"] == "RFU" and st["has_result"]
        assert _ok(other.get("/api/assay/results"))["rows"]


def test_malformed_session_import_leaves_session_untouched(client):
    _ok(client.post("/api/solid_state/files/sample"))
    bad = json.dumps({"conc_unit": "nM", "amp_files": [{"filename": "x"}]})
    r = client.post("/api/session/import", files={"file": ("s.json", bad, "application/json")})
    assert r.status_code == 400
    assert len(_ok(client.get("/api/solid_state/state"))["files"]) == 1
    assert _ok(client.get("/api/amperometry/state"))["conc_unit"] == "mM"


def test_streamlit_bundle_assay_result_with_nan_renders(client):
    """Streamlit exports assay_std_res arrays with NaN (not null) — must still work."""
    from modes.assay import parse_plate_csv
    from web_app.api.assay import SAMPLE_PLATE, compute_standard_curve
    from web_app.session import default_assay_std_df
    plate = parse_plate_csv(open(SAMPLE_PLATE).read())
    res, _ = compute_standard_curve(plate, default_assay_std_df(), "Linear")
    res["sds"] = [float("nan") if v is None else v for v in res["sds"]]
    bundle = {"assay_plate": plate.to_csv(), "assay_std_df": default_assay_std_df().to_dict(orient="records"),
              "assay_sample_df": [], "assay_std_res": res}
    _ok(client.post("/api/session/import", files={"file": ("s.json", json.dumps(bundle), "application/json")}))
    assert _ok(client.get("/api/assay/curve"))["figure"]


def test_plain_csv_upload_maps_every_signal_column(client):
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "sensor_run_A.csv"), "rb") as fh:
        state = _ok(client.post("/api/amperometry/files", files=[("files", ("sensor_run_A.csv", fh, "text/csv"))]))
    assert state["channel_labels"] == ["Channel A", "Channel B"]



def test_cross_site_and_rebinding_requests_are_blocked(client):
    assert client.post("/api/app/quit", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/assay/state", headers={"Host": "attacker.example:8000"}).status_code == 403
    # same-origin requests (what the page itself sends) still work
    assert client.post("/api/assay/units", json={"sig_unit": "Abs"},
                       headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/assay/state", headers={"Host": "localhost:8000"}).status_code == 200
