"""Assay: excluding wells, curve QC (LOD/LOQ, recovery), several plates,
more than 3 standard replicates, and layout templates — plus how each of
these appears in the Streamlit-compatible session bundle."""
import io
import json

import pytest

pytest.importorskip("fastapi")
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


def _fit(client, fit="Linear"):
    return _ok(client.post("/api/assay/compute", json={"fit_type": fit}))


def _export(client):
    return json.loads(client.get("/api/session/export").content)


def _import(client, bundle):
    return _ok(client.post("/api/session/import",
                           files={"file": ("s.json", json.dumps(bundle).encode(), "application/json")}))


# -- exclusions ------------------------------------------------------------------
def test_excluding_a_standard_well_refits_and_shows_on_the_plate(client):
    _ok(client.post("/api/assay/plate/sample"))
    before = _fit(client)
    st = _ok(client.post("/api/assay/exclude", json={"wells": ["A4"]}))
    assert st["excluded"] == ["A4"] and "refitted" in st["message"]
    assert next(w for w in st["wells"] if w["well"] == "A4")["excluded"]
    after = _ok(client.get("/api/assay/curve"))
    assert after["equation"] != before["equation"]
    assert "excl." in json.dumps(after["standards"])
    st = _ok(client.post("/api/assay/exclude", json={"wells": ["A4"], "excluded": False}))
    assert st["excluded"] == [] and _ok(client.get("/api/assay/curve"))["equation"] == before["equation"]


def test_excluded_sample_wells_are_listed_but_left_out_of_means(client):
    _ok(client.post("/api/assay/plate/sample"))
    _fit(client)
    r = _ok(client.get("/api/assay/results"))
    g = next(g for g in r["groups"] if g["Subject"] == "P01" and g["Timepoint"] == "D0")
    assert g["n"] == 2
    _ok(client.post("/api/assay/exclude", json={"wells": ["D1"]}))
    r = _ok(client.get("/api/assay/results"))
    g = next(g for g in r["groups"] if g["Subject"] == "P01" and g["Timepoint"] == "D0")
    assert g["n"] == 1 and "1 excluded" in g["Flag"] and "D1 (excl.)" in g["Wells"]
    row = next(x for x in r["rows"] if x["Well"] == "D1")
    assert row["Excluded"] and row["Conc"] == "—"
    assert "1 excluded" in r["summary"]


def test_exclusions_survive_export_and_reimport(client):
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/assay/exclude", json={"wells": ["E2", "B3"]}))
    bundle = _export(client)
    assert bundle["assay_plates"][0]["excluded"] == ["B3", "E2"]
    with TestClient(app, base_url="http://127.0.0.1") as other:
        _import(other, bundle)
        assert _ok(other.get("/api/assay/state"))["excluded"] == ["B3", "E2"]


# -- QC ----------------------------------------------------------------------------
def test_curve_qc_reports_lod_loq_and_recovery(client):
    _ok(client.post("/api/assay/plate/sample"))
    res = _fit(client, "4-Parameter Logistic (4PL)")
    qc = res["qc"]
    assert qc["n_blank"] == 3 and qc["lod"] != "—" and qc["loq"] != "—"
    lod = float(qc["lod"].split()[0])
    loq = float(qc["loq"].split()[0])
    assert 0 < lod < loq
    rec = [r["Recovery (%)"] for r in res["standards"][1:]]
    assert all(r != "—" for r in rec)
    csv = client.get("/api/assay/export/standards-csv").text
    assert "# LOD:" in csv and "Recovery (%)" in csv


def test_lod_is_none_with_a_single_blank_replicate():
    from core.analysis.assay import compute_standard_curve, parse_plate_csv, SAMPLE_PLATE
    from web_app.session import default_assay_std_df
    std = default_assay_std_df()
    std.loc[0, ["S2", "S3"]] = ""
    res, _ = compute_standard_curve(parse_plate_csv(open(SAMPLE_PLATE).read()), std, "Linear")
    assert res["qc"]["lod"] is None and res["qc"]["n_blank"] == 1


def test_sample_below_lod_is_flagged():
    import numpy as np
    from core.analysis.assay import empty_plate, sample_results
    plate = empty_plate()
    plate.loc["H", 1] = 0.101
    res = {"fit": {"type": "linear", "slope": 0.1, "intercept": 0.0, "r2": 1.0}, "blank_mean": 0.1,
           "concs": [0.0, 1.0, 10.0], "std_df": [], "qc": {"lod": 0.5, "loq": 1.5}}
    rows = sample_results(plate, res, {"H1": "x"}, std_wells=set())
    # The range starts at the blank's 0, as in the Streamlit app, so only the LOD flag applies.
    assert rows[0]["Flag"] == "< LOD" and np.isclose(rows[0]["Conc"], 0.01)
    plate.loc["H", 1] = 0.05   # below the blank: negative concentration
    assert sample_results(plate, res, {"H1": "x"}, std_wells=set())[0]["Flag"] == "< range, < LOD"


# -- more than 3 replicates ----------------------------------------------------------
def test_four_replicate_standards_fit_and_export_streamlit_safe(client):
    _ok(client.post("/api/assay/plate/sample"))
    wells = [f"{r}{c}" for r in "ABCD" for c in range(1, 9)]
    st = _ok(client.post("/api/assay/assign/standards", json={
        "wells": wells, "direction": "across", "mode": "serial", "top": 100, "factor": 2, "include_blank": True}))
    assert st["rep_cols"] == ["S1", "S2", "S3", "S4"] and st["std_rows"][1]["S4"].startswith("D")
    res = _fit(client)
    assert any("S4 raw" in k for k in res["standards"][0])
    bundle = _export(client)
    # Streamlit only knows S1–S3, and its fit would differ — so the flat
    # keys carry 3 replicates and no fit, while the web-only plate keeps all.
    assert set(bundle["assay_std_df"][0]) == {"Label", "Conc", "S1", "S2", "S3"}
    assert bundle["assay_std_res"] is None
    assert "S4" in bundle["assay_plates"][0]["std_df"][0]
    with TestClient(app, base_url="http://127.0.0.1") as other:
        _import(other, bundle)
        assert _ok(other.get("/api/assay/state"))["rep_cols"][-1] == "S4"
        assert _ok(other.get("/api/assay/curve"))["figure"]


def test_layout_table_accepts_extra_replicate_columns(client):
    _ok(client.post("/api/assay/plate/sample"))
    rows = _ok(client.get("/api/assay/state"))["std_rows"]
    rows[1]["S4"] = "H12"
    st = _ok(client.post("/api/assay/layout", json={"std_rows": rows, "sample_rows": []}))
    assert st["rep_cols"] == ["S1", "S2", "S3", "S4"]
    rows[1]["S13"] = "H11"
    assert client.post("/api/assay/layout", json={"std_rows": rows, "sample_rows": []}).status_code == 400


# -- several plates ----------------------------------------------------------------------
def _second_plate_with_borrowed_curve(client):
    _ok(client.post("/api/assay/plate/sample"))
    _fit(client)
    st = _ok(client.post("/api/assay/plates", json={"name": "Plate B", "copy_layout": False}))
    assert st["active"] == 1 and [p["name"] for p in st["plates"]] == ["Plate 1", "Plate B"]
    # Same readings, but only samples on this plate: P06 at timepoints D0/D3.
    grid = _ok(client.get("/api/assay/state"))["plate"] or [[None] * 12 for _ in range(8)]
    for c in range(4):
        grid[7][c] = 0.3 + 0.05 * c
    _ok(client.post("/api/assay/plate", json={"grid": grid}))
    _ok(client.post("/api/assay/assign/samples", json={"wells": ["H1", "H2", "H3", "H4"], "subjects": "P06",
                                                       "timepoints": "D0, D3", "replicates": 2}))
    assert client.post("/api/assay/compute", json={"fit_type": "Linear"}).status_code == 400  # no standards here
    return _ok(client.post("/api/assay/plates/curve-source", json={"source": "p1"}))


def test_a_plate_can_borrow_another_plates_curve_and_results_pool(client):
    st = _second_plate_with_borrowed_curve(client)
    assert st["curve_source"] == "Plate 1" and st["has_curve"]
    assert _ok(client.get("/api/assay/curve"))["source"] == "Plate 1"
    r = _ok(client.get("/api/assay/results"))
    assert r["multi_plate"] and {x["Plate"] for x in r["rows"]} == {"Plate 1", "Plate B"}
    subjects = {g["Subject"] for g in r["groups"]}
    assert {"P01", "P05", "P06"} <= subjects
    # Fitting through the borrowing plate is refused; switching back works.
    assert client.post("/api/assay/compute", json={"fit_type": "Linear"}).status_code == 400
    assert _ok(client.post("/api/assay/plates/0/activate"))["active"] == 0


def test_plates_round_trip_and_flat_keys_stay_streamlit_shaped(client):
    _second_plate_with_borrowed_curve(client)
    bundle = _export(client)
    assert len(bundle["assay_plates"]) == 2 and bundle["assay_active"] == 1
    assert bundle["assay_std_res"] is None  # the active plate's borrowed fit can't be expressed in Streamlit
    with TestClient(app, base_url="http://127.0.0.1") as other:
        _import(other, bundle)
        st = _ok(other.get("/api/assay/state"))
        assert [p["name"] for p in st["plates"]] == ["Plate 1", "Plate B"] and st["curve_source"] == "Plate 1"
        assert _ok(other.get("/api/assay/results"))["multi_plate"]


def test_rename_and_delete_plates(client):
    _second_plate_with_borrowed_curve(client)
    assert client.post("/api/assay/plates/1/rename", json={"name": "Plate 1"}).status_code == 400
    _ok(client.post("/api/assay/plates/1/rename", json={"name": "Day 2"}))
    st = _ok(client.delete("/api/assay/plates/0"))
    assert [p["name"] for p in st["plates"]] == ["Day 2"] and st["active"] == 0
    assert st["plates"][0]["std_source"] is None  # its curve source is gone
    assert client.delete("/api/assay/plates/0").status_code == 400  # never remove the last plate


def test_a_streamlit_bundle_resets_to_one_plate(client):
    _second_plate_with_borrowed_curve(client)
    bundle = _export(client)
    for key in ("assay_plates", "assay_active", "assay_norm", "assay_readout"):
        bundle.pop(key)
    _import(client, bundle)
    st = _ok(client.get("/api/assay/state"))
    assert len(st["plates"]) == 1 and st["plates"][0]["name"] == "Plate 1"


# -- templates ---------------------------------------------------------------------------
def test_templates_save_apply_download_upload_delete(client):
    _ok(client.post("/api/assay/plate/sample"))
    _ok(client.post("/api/assay/readout", json={"type": "fluorescence", "ex": "485", "em": "528"}))
    t = _ok(client.post("/api/assay/templates", json={"name": "ELISA 8pt"}))
    assert [x["name"] for x in t["templates"]] == ["ELISA 8pt"] and t["templates"][0]["n_samples"] == 40
    _ok(client.delete("/api/assay/plate"))
    _ok(client.post("/api/assay/assign/clear", json={"wells": [f"{r}{c}" for r in "ABCDEFGH" for c in range(1, 13)]}))
    _ok(client.post("/api/assay/readout", json={"type": "absorbance"}))
    st = _ok(client.post("/api/assay/templates/apply", json={"name": "ELISA 8pt"}))
    assert len(st["sample_rows"]) == 40 and st["readout"]["ex"] == "485" and st["sig_unit"] == "RFU"
    raw = client.get("/api/assay/templates/ELISA 8pt/download").content
    _ok(client.delete("/api/assay/templates/ELISA 8pt"))
    assert _ok(client.get("/api/assay/templates"))["templates"] == []
    up = _ok(client.post("/api/assay/templates/upload", files={"file": ("x.json", io.BytesIO(raw), "application/json")}))
    assert [x["name"] for x in up["templates"]] == ["ELISA 8pt"]
    bad = client.post("/api/assay/templates/upload", files={"file": ("x.json", b"{}", "application/json")})
    assert bad.status_code == 400
