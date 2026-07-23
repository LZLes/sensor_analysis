import numpy as np
import pytest

from app.analysis.plate import parse_plate_csv, plate_get, well_rc


def test_well_rc_parses_valid_wells():
    assert well_rc("A1") == (0, 0)
    assert well_rc("H12") == (7, 11)
    assert well_rc("b5") == (1, 4)


def test_well_rc_invalid_returns_none():
    assert well_rc("Z1") is None
    assert well_rc("A13") is None
    assert well_rc("") is None


def test_parse_plate_csv_basic_grid():
    raw = "A,1.0,2.0,3.0\nB,4.0,5.0,6.0\n"
    df = parse_plate_csv(raw)
    assert df.shape == (8, 12)
    assert df.loc["A", 1] == pytest.approx(1.0)
    assert df.loc["B", 3] == pytest.approx(6.0)
    assert np.isnan(df.loc["C", 1])


def test_parse_plate_csv_no_rows_raises():
    with pytest.raises(ValueError):
        parse_plate_csv("nothing,here\nat,all\n")


def test_plate_get_reads_from_parsed_grid():
    raw = "A,10.0,20.0\n"
    df = parse_plate_csv(raw)
    assert plate_get(df, "A1") == pytest.approx(10.0)
    assert np.isnan(plate_get(df, "H12"))
    assert np.isnan(plate_get(None, "A1"))
