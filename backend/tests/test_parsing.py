import os

import numpy as np
import pandas as pd
import pytest

from app.analysis.parsing import parse_potentiostat_csv

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")


def test_standard_csv_parses_directly_with_pandas():
    # sample_data/*.csv are plain "Standard CSV" (single header row, no
    # metadata/units rows) — the app's own T1 flow reads these with
    # pd.read_csv directly, not parse_potentiostat_csv. Confirm the fixture
    # still looks like what the rest of these tests assume.
    path = os.path.join(SAMPLE_DIR, "sensor_run_A.csv")
    df = pd.read_csv(path)
    assert list(df.columns) == ["Time (s)", "Channel A (uA)", "Channel B (uA)"]
    assert len(df) == 301


def test_parse_potentiostat_csv_with_metadata_and_units_rows():
    raw = (
        "Experiment: demo\n"
        "Notes: \n"
        "CH1: Sensor A,,CH2: Sensor B,\n"
        "s,uA,s,uA\n"
        "0,1.0,0,2.0\n"
        "1,1.1,1,2.1\n"
        "2,1.2,2,2.2\n"
    )
    df, channels = parse_potentiostat_csv(raw, sep=",")
    assert len(df) == 3
    assert len(channels) == 2
    names = {c["name"] for c in channels}
    assert names == {"CH1", "CH2"}
    for c in channels:
        assert "tc" in c and "ic" in c


def test_parse_potentiostat_csv_cv_mode_uses_voltage_column():
    raw = (
        "CH1: E1,,\n"
        "V,uA\n"
        "-0.5,0.01\n"
        "0.0,0.05\n"
        "0.5,0.02\n"
    )
    df, channels = parse_potentiostat_csv(raw, sep=",", mode="cv")
    assert len(channels) == 1
    assert "vc" in channels[0]
    assert "tc" not in channels[0]


def test_parse_potentiostat_csv_no_numeric_rows_raises():
    with pytest.raises(ValueError):
        parse_potentiostat_csv("just,some,text\nmore,text,here\n", sep=",")


def test_parse_potentiostat_csv_comma_decimal_separator():
    raw = (
        "CH1: A,\n"
        "s,uA\n"
        "0,1;5\n"
        "1,2;5\n"
    ).replace(";", ",")   # build "1,5" style values without confusing the column sep
    # Use semicolon as the field separator so comma can be the decimal mark
    raw2 = "CH1: A,\ns,uA\n0;1,5\n1;2,5\n"
    df, channels = parse_potentiostat_csv(raw2, sep=";")
    assert df.iloc[0, 1] == pytest.approx(1.5)
    assert df.iloc[1, 1] == pytest.approx(2.5)
