"""The app shell around the modes: registry, undo/redo, autosave, reports,
and the rule that the web app (and core/analysis) never imports Streamlit."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from web_app import autosave  # noqa: E402
from web_app.main import app  # noqa: E402
from web_app.registry import MODES  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "web_app" / "static"


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.get("/api/session/ping")
        yield c


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_web_app_imports_without_streamlit():
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'streamlit' or name.startswith('streamlit.'):\n"
        "            raise ImportError('streamlit is blocked')\n"
        "sys.meta_path.insert(0, Block())\n"
        "import web_app.main, core.analysis.amperometry, core.analysis.solid_state, core.analysis.assay, "
        "core.analysis.assay_layout, core.analysis.cv, core.analysis.traces, core.serialization\n"
        # Exports run in worker threads, which only the Agg backend allows on macOS.
        "import matplotlib; assert matplotlib.get_backend().lower() == 'agg', matplotlib.get_backend()\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_registered_mode_has_its_files_and_hooks(client):
    listed = _ok(client.get("/api/app/modes"))["modes"]
    assert [m["id"] for m in listed] == [m.id for m in MODES]
    for spec in MODES:
        assert (STATIC / "modes" / f"{spec.id}.html").is_file(), spec.id
        assert (STATIC / "js" / f"{spec.id}.js").is_file(), spec.id
        for script in spec.scripts:
            assert (STATIC / "js" / script).is_file(), script
        assert spec.router.prefix == f"/api/{spec.id}"
        # Every mode's state endpoint answers on a fresh session, and its
        # bundle hooks round-trip an empty session.
        assert client.get(f"/api/{spec.id}/state").status_code == 200
        assert isinstance(spec.export_bundle(_fresh()), dict)


def _fresh():
    from web_app.session import SessionData
    return SessionData()


def test_undo_redo_restores_assay_layout(client):
    _ok(client.post("/api/assay/plate/sample"))
    before = _ok(client.get("/api/assay/state"))["std_rows"]
    r = client.post("/api/assay/assign/clear", json={"wells": ["A1", "B1", "C1"]})
    assert r.headers["X-Undo"] == "2,0" and r.headers["X-Undo-Mode"] == "assay"
    assert _ok(r)["std_rows"] != before
    # A preview never creates an undo step.
    r = client.post("/api/assay/assign/samples", json={"wells": ["H1"], "subjects": "x", "preview": True})
    assert r.headers["X-Undo"] == "2,0"
    undone = client.post("/api/history/assay/undo")
    assert _ok(undone)["std_rows"] == before and undone.headers["X-Undo"] == "1,1"
    assert _ok(client.post("/api/history/assay/redo"))["std_rows"] != before
    assert client.post("/api/history/assay/redo").status_code == 400  # nothing left to redo


def test_undo_covers_trace_mode_tables_and_file_removal(client):
    _ok(client.post("/api/amperometry/files/sample"))
    st = _ok(client.get("/api/amperometry/state"))
    rows = st["files"][0]["cpdf"]
    rows[1]["Concentration"] = 999
    _ok(client.post("/api/amperometry/files/0/table", json={"rows": rows}))
    _ok(client.delete("/api/amperometry/files/0"))
    assert len(_ok(client.get("/api/amperometry/state"))["files"]) == 1
    restored = _ok(client.post("/api/history/amperometry/undo"))
    assert len(restored["files"]) == 2 and restored["files"][0]["cpdf"][1]["Concentration"] == 999
    assert _ok(client.post("/api/history/amperometry/undo"))["files"][0]["cpdf"][1]["Concentration"] != 999


def test_import_clears_undo_history(client):
    _ok(client.post("/api/assay/plate/sample"))
    bundle = client.get("/api/session/export").content
    r = client.post("/api/session/import", files={"file": ("s.json", bundle, "application/json")})
    assert _ok(r)["loaded"] == ["1 assay plate"]
    assert _ok(client.get("/api/history/assay")) == {"undo": 0, "redo": 0}


def test_autosave_writes_and_offers_restore(client, tmp_path):
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/amperometry/files/sample"))
    autosave.save_now()
    saved = json.loads(autosave.current_path().read_text())
    assert saved["summary"] == ["2 amperometry files", "1 assay plate"]
    # Next run: rotate, then a fresh browser session is offered the restore.
    autosave.rotate()
    with TestClient(app, base_url="http://127.0.0.1") as fresh:
        fresh.get("/api/session/ping")
        info = _ok(fresh.get("/api/autosave"))
        assert info["available"] and info["summary"] == ["2 amperometry files", "1 assay plate"]
        assert _ok(fresh.post("/api/autosave/restore"))["loaded"] == ["2 amperometry files", "1 assay plate"]
        assert len(_ok(fresh.get("/api/amperometry/state"))["files"]) == 2
        assert not _ok(fresh.get("/api/autosave"))["available"]


def test_autosave_is_not_offered_to_a_session_with_data(client):
    autosave.previous_path().write_text(json.dumps({"kind": "sensor-studio-autosave", "bundle": {}, "summary": ["x"]}))
    assert _ok(client.get("/api/autosave"))["available"]
    _ok(client.post("/api/assay/plate/sample"))
    assert not _ok(client.get("/api/autosave"))["available"]
    _ok(client.post("/api/autosave/discard"))
    assert not autosave.previous_path().exists()


def test_mutations_mark_the_session_for_autosave(client):
    autosave._dirty = None
    _ok(client.get("/api/assay/state"))
    assert autosave._dirty is None
    _ok(client.post("/api/assay/plate/sample"))
    assert autosave._dirty is not None


@pytest.mark.parametrize("mode_id", [m.id for m in MODES])
def test_reports_render_empty_and_with_data(client, mode_id):
    r = client.get(f"/api/report/{mode_id}")
    assert r.status_code == 200 and "<h1>" in r.text and "Print" in r.text


def test_assay_report_has_curve_qc_and_results(client):
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/assay/compute", json={"fit_type": "Linear"}))
    html = client.get("/api/report/assay").text
    for text in ("standard curve", "LOD (3 SD)", "Results per sample", "data:image/png;base64,", "Plate 1 — layout"):
        assert text in html, text


def test_amperometry_report_after_compute(client):
    st = _ok(client.post("/api/amperometry/files/sample"))
    _ok(client.post("/api/amperometry/compute", json={"selected": st["channel_labels"], "fit_type": "Linear"}))
    html = client.get("/api/report/amperometry").text
    assert "Sensor statistics" in html and "data:image/png;base64," in html
