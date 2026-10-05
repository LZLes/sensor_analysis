"""Pure-function tests for web_app/api/assay_layout.py (plate layout,
subject/timepoint labelling, grouping, normalisation)."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fastapi")

from web_app.api import assay_layout as lay  # noqa: E402
from web_app.session import default_assay_sample_df, default_assay_std_df  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("P01-P03", ["P01", "P02", "P03"]),
    ("P1 - P3", ["P1", "P2", "P3"]),
    ("D0, D3,D7\nD14", ["D0", "D3", "D7", "D14"]),
    ("Day 0 - Day 14 by 7", ["Day 0", "Day 7", "Day 14"]),
    ("0h-12h by 6", ["0h", "6h", "12h"]),
    ("WT-1, 2024-01, KO", ["WT-1", "2024-01", "KO"]),   # not ranges: kept literally
    ("", []),
])
def test_parse_name_list(text, expected):
    assert lay.parse_name_list(text) == expected


def test_serial_concs():
    assert lay.serial_concs(4, 100, 2, True, False) == [100, 50, 25, 0]
    assert lay.serial_concs(3, 9, 3, False, True) == [1, 3, 9]
    with pytest.raises(ValueError):
        lay.serial_concs(3, 100, 1, True, False)


def test_assign_standards_puts_blank_first_and_removes_sample_labels():
    samples = pd.DataFrame([{"Well": "A2", "Label": "x", "Subject": "x", "Timepoint": ""}])
    wells = [f"{r}{c}" for r in "AB" for c in (1, 2, 3)]          # 3 levels (columns) × 2 reps
    std, s, _ = lay.assign_standards(default_assay_std_df(), samples, wells, "across", [10, 5, 0])
    assert std["Label"].tolist() == ["Blank", "Std 5", "Std 10"]
    assert std.iloc[0][["S1", "S2", "S3"]].tolist() == ["A3", "B3", ""]
    assert s.empty


def test_assign_standards_rejects_more_than_three_replicates_and_count_mismatch():
    wells = [f"{r}1" for r in "ABCD"]
    with pytest.raises(ValueError, match="at most 3"):
        lay.assign_standards(default_assay_std_df(), default_assay_sample_df(), wells, "across", [1])
    with pytest.raises(ValueError, match="2 concentration"):
        lay.assign_standards(default_assay_std_df(), default_assay_sample_df(), ["A1", "A2", "A3"], "across", [1, 2])


def test_assign_standards_without_zero_keeps_existing_blank():
    std, _, msg = lay.assign_standards(default_assay_std_df(), default_assay_sample_df(),
                                       ["D1", "D2"], "across", [1, 2])
    assert std.iloc[0]["Label"] == "Blank" and std.iloc[0]["S1"] == "A1"
    assert "Kept the existing blank" in msg


def test_plan_samples_nesting_order_and_replicates():
    wells = [f"D{c}" for c in range(1, 9)]
    plan, msg = lay.plan_samples(wells, ["P1", "P2"], ["T0", "T1"], 2, "rows", "subject")
    assert [(p["Well"], p["Label"]) for p in plan[::2]] == [("D1", "P1 · T0"), ("D3", "P1 · T1"),
                                                             ("D5", "P2 · T0"), ("D7", "P2 · T1")]
    plan, _ = lay.plan_samples(wells, ["P1", "P2"], ["T0", "T1"], 2, "rows", "timepoint")
    assert plan[2]["Label"] == "P2 · T0"
    plan, msg = lay.plan_samples(wells[:5], ["P1", "P2"], ["T0", "T1"], 2, "rows", "subject")
    assert len(plan) == 5 and "Not enough wells" in msg
    plan, _ = lay.plan_samples(["A1", "B1", "A2", "B2"], ["S"], ["a", "b"], 2, "columns", "subject")
    assert [p["Well"] for p in plan if p["Timepoint"] == "a"] == ["A1", "B1"]


def test_assign_samples_takes_wells_out_of_standards():
    wells = ["A2", "B2"]
    plan, _ = lay.plan_samples(wells, ["P1"], [], 2, "rows", "subject")
    std, s = lay.assign_samples(default_assay_std_df(), default_assay_sample_df(), plan, wells)
    assert std.iloc[1][["S1", "S2", "S3"]].tolist() == ["", "", "C2"]
    assert s["Well"].tolist() == ["A2", "B2"] and s["Label"].tolist() == ["P1", "P1"]


def test_assign_blank_replaces_row_zero_and_drops_emptied_levels():
    std, _, _ = lay.assign_blank(default_assay_std_df(), default_assay_sample_df(), ["A2", "B2", "C2"])
    assert std.iloc[0][["Label", "Conc", "S1"]].tolist() == ["Blank", 0.0, "A2"]
    assert "Std 2" not in std["Label"].tolist() and len(std) == 7
    with pytest.raises(ValueError):
        lay.assign_blank(default_assay_std_df(), default_assay_sample_df(), ["H1", "H2", "H3", "H4"])


def test_layout_grid_paste_with_headers_standards_and_samples():
    text = "\t1\t2\t3\nA\tBlank\tStd 10\tP01_D7\nB\tblank\tSTD 10\tP01_D7\nC\t\tStd: 2.5\tP02 Day 3\n"
    cells = lay.parse_layout_grid(text)
    std, samples, _ = lay.apply_layout_grid(default_assay_std_df(), cells, "auto")
    assert std["Label"].tolist() == ["Blank", "Std 2.5", "Std 10"]
    assert std.iloc[2][["S1", "S2"]].tolist() == ["A2", "B2"]
    assert samples[["Well", "Subject", "Timepoint"]].values.tolist() == [
        ["A3", "P01", "D7"], ["B3", "P01", "D7"], ["C3", "P02", "Day 3"]]


def test_group_results_and_normalise():
    rows = [{"Well": "D1", "Label": "", "Subject": "P1", "Timepoint": "T0", "Conc": 2.0, "Flag": ""},
            {"Well": "D2", "Label": "", "Subject": "P1", "Timepoint": "T0", "Conc": 4.0, "Flag": "> range"},
            {"Well": "D3", "Label": "", "Subject": "P2", "Timepoint": "T0", "Conc": None, "Flag": "undefined"}]
    g = lay.group_results(rows)
    assert g[0]["n"] == 2 and g[0]["Mean"] == 3.0 and g[0]["Flag"] == "> range"
    assert g[1]["n"] == 0 and np.isnan(g[1]["Mean"])
    out, units = lay.normalise(g, [{"Subject": "P1", "Timepoint": "T0", "Dilution": 10, "Volume": 50, "Area": 2}],
                               "µM", "µL", "cm²")
    assert out[0]["SampleConc"] == 30 and out[0]["Amount"] == 1500 and out[0]["PerArea"] == 750
    assert units["amount"] == "pmol" and units["per_area"] == "pmol/cm²"
    # No volume anywhere: per area = sample conc / area
    out, units = lay.normalise(g, [{"Subject": "P1", "Timepoint": "T0", "Area": 2}], "ng/mL", "µL", "cm²")
    assert out[0]["PerArea"] == 1.5 and units["per_area"] == "ng/mL/cm²"


@pytest.mark.parametrize("cu, vu, expected", [
    ("µM", "µL", (1.0, "pmol")), ("nM", "mL", (1.0, "pmol")), ("ng/mL", "µL", (1.0, "pg")),
    ("mM", "µL", (1.0, "nmol")), ("mg/dL", "µL", (10.0, "ng")), ("AU", "µL", (1.0, "AU·µL")),
])
def test_amount_unit(cu, vu, expected):
    f, u = lay.amount_unit(cu, vu)
    assert (round(f, 9), u) == expected


def test_old_streamlit_sample_table_is_normalised():
    df = pd.DataFrame([{"Well": "d1", "Label": "Patient 1"}, {"Well": None, "Label": "x"}])
    out = lay.normalize_sample_df(df)
    assert out.to_dict(orient="records") == [{"Well": "D1", "Label": "Patient 1", "Subject": "", "Timepoint": ""}]
