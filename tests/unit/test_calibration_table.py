import numpy as np
import pandas as pd

from core.calibration_table import (
    _default_cpdf, _cpdf_from_records, _baseline_keep_mask,
    _default_solid_cpdf, _solid_cpdf_from_records,
)


def test_default_cpdf_shape():
    df = _default_cpdf()
    assert list(df.columns) == [
        "Label", "Concentration", "Spike Vol", "Stock Conc",
        "t_start", "t_end", "avg_duration", "Baseline",
    ]
    assert df["Baseline"].tolist() == [True, False, False, False]


def test_cpdf_from_records_empty_falls_back_to_default():
    out = _cpdf_from_records(None)
    pd.testing.assert_frame_equal(out, _default_cpdf())
    out2 = _cpdf_from_records([])
    pd.testing.assert_frame_equal(out2, _default_cpdf())


def test_cpdf_from_records_coerces_numeric_and_baseline():
    records = [
        {"Label": "Blank", "Concentration": "0", "t_start": "0", "t_end": "10", "Baseline": True},
        {"Label": "Step 1", "Concentration": "1.5", "t_start": "10", "t_end": "20", "Baseline": None},
    ]
    out = _cpdf_from_records(records)
    assert out["Concentration"].tolist() == [0.0, 1.5]
    assert out["Baseline"].tolist() == [True, False]   # None -> False, not NaN-truthy


def test_cpdf_from_records_missing_label_gets_generated():
    records = [{"Concentration": 1.0, "t_start": 0.0, "t_end": 10.0}]
    out = _cpdf_from_records(records)
    assert out["Label"].tolist() == ["Row 1"]


def test_baseline_keep_mask_excludes_true_and_nan_is_not_baseline():
    # NaN must NOT be treated as baseline=True (regression for the bug where
    # `not bool(nan)` silently excluded NaN rows since bool(nan) is True).
    mask = _baseline_keep_mask([True, False, np.nan, None])
    assert mask == [False, True, True, True]


def test_default_solid_cpdf_shape_has_no_baseline_or_spike_columns():
    df = _default_solid_cpdf()
    assert "Baseline" not in df.columns
    assert "Spike Vol" not in df.columns
    assert "Reading_mV" in df.columns


def test_solid_cpdf_from_records_empty_uses_solid_default_not_amp_default():
    out = _solid_cpdf_from_records(None)
    pd.testing.assert_frame_equal(out, _default_solid_cpdf())
    assert "Baseline" not in out.columns


def test_solid_cpdf_from_records_does_not_add_amp_only_columns():
    records = [{"Label": "Std 1", "Concentration": "1.0", "t_start": "0", "t_end": "10", "Reading_mV": "5.2"}]
    out = _solid_cpdf_from_records(records)
    assert "Spike Vol" not in out.columns
    assert "Stock Conc" not in out.columns
    assert "Baseline" not in out.columns
    assert out["Reading_mV"].tolist() == [5.2]
    assert out["Concentration"].dtype == float
