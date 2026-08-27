import io
import zipfile

import numpy as np
import pytest

from core.parsing import parse_potentiostat_csv, parse_pssession, _ps_unit


def test_parse_potentiostat_csv_amperometry():
    raw = (
        "Date,2024-01-01,,\n"
        "Notes,test run,,\n"
        "CH1: Sensor A,,CH2: Sensor B,\n"
        "s,µA,s,µA\n"
        "0,1.0,0,2.0\n"
        "1,1.1,1,2.1\n"
        "2,1.2,2,2.2\n"
    )
    df, channels = parse_potentiostat_csv(raw, sep=",")
    assert len(channels) == 2
    names = {c["name"] for c in channels}
    assert names == {"CH1", "CH2"}
    for ch in channels:
        assert "tc" in ch and "ic" in ch
    assert len(df) == 3
    ch1 = next(c for c in channels if c["name"] == "CH1")
    assert df[ch1["ic"]].tolist() == [1.0, 1.1, 1.2]


def test_parse_potentiostat_csv_cv_mode_uses_voltage_units():
    raw = (
        "Date,x\n"
        "CH1: Cell,\n"
        "V,µA\n"
        "-0.5,1.0\n"
        "0.0,2.0\n"
        "0.5,1.5\n"
    )
    df, channels = parse_potentiostat_csv(raw, sep=",", mode="cv")
    assert len(channels) == 1
    assert "vc" in channels[0] and "ic" in channels[0]


def test_parse_potentiostat_csv_no_numeric_data_raises():
    raw = "a,b,c\nd,e,f\n"
    with pytest.raises(ValueError):
        parse_potentiostat_csv(raw, sep=",")


def test_parse_potentiostat_csv_comma_decimal_handled():
    # Semicolon-delimited so "1,5" (comma-decimal) survives as one field.
    raw = "CH1: A;\ns;µA\n0;1,5\n1;2,5\n"
    df, channels = parse_potentiostat_csv(raw, sep=";")
    ch = channels[0]
    assert df[ch["ic"]].tolist() == [1.5, 2.5]


def _ps_zip_with_points():
    xml = b"""<?xml version="1.0"?>
<Root>
  <Curve Title="CH1">
    <XUnit>s</XUnit>
    <YUnit>uA</YUnit>
    <Point X="0" Y="1.0"/>
    <Point X="1" Y="1.5"/>
    <Point X="2" Y="2.0"/>
  </Curve>
</Root>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("session.xml", xml)
    return buf.getvalue()


def test_parse_pssession_curve_points_strategy():
    df, channels = parse_pssession(_ps_zip_with_points())
    assert len(channels) == 1
    ch = channels[0]
    assert df[ch["tc"]].tolist() == [0.0, 1.0, 2.0]
    assert df[ch["ic"]].tolist() == [1.0, 1.5, 2.0]


def test_parse_pssession_values_strategy():
    xml = b"""<?xml version="1.0"?>
<Root>
  <Values>
    <Value>0,1.0</Value>
    <Value>1,1.2</Value>
  </Values>
</Root>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.xml", xml)
    df, channels = parse_pssession(buf.getvalue())
    assert len(channels) == 1


def test_parse_pssession_plain_xml_not_zip():
    xml = b"""<?xml version="1.0"?>
<Root>
  <Values>
    <Value>0,1.0</Value>
    <Value>1,1.2</Value>
  </Values>
</Root>"""
    df, channels = parse_pssession(xml)
    assert len(channels) == 1


def test_parse_pssession_no_recognizable_data_raises():
    xml = b"<Root><Unrelated/></Root>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("session.xml", xml)
    with pytest.raises(ValueError):
        parse_pssession(buf.getvalue())


def test_ps_unit_extracts_bare_unit():
    assert _ps_unit("Time / s", "x") == "s"
    assert _ps_unit("µA", "x") == "µA"
    assert _ps_unit(None, "fallback") == "fallback"
    assert _ps_unit("", "fallback") == "fallback"
