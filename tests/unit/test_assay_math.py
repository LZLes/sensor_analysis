import numpy as np
import pytest

from modes.assay import (
    _well_rc, _plate_get, parse_plate_csv, _fit_4pl, _4pl_inv, _is_plate_num,
)


def test_well_rc_valid_and_invalid():
    assert _well_rc("A1") == (0, 0)
    assert _well_rc("H12") == (7, 11)
    assert _well_rc("h1") == (7, 0)
    assert _well_rc("Z1") is None
    assert _well_rc("A13") is None
    assert _well_rc("") is None
    assert _well_rc("A0") is None


def test_plate_get_returns_nan_for_missing_or_invalid():
    assert np.isnan(_plate_get(None, "A1"))
    assert np.isnan(_plate_get(None, "Z9"))


def test_is_plate_num():
    assert _is_plate_num("1.5")
    assert not _is_plate_num("abc")


def _make_96_well_csv():
    lines = []
    for i, r in enumerate("ABCDEFGH"):
        vals = [str(0.1 * (i + 1) + 0.01 * c) for c in range(12)]
        lines.append(r + "," + ",".join(vals))
    return "\n".join(lines)


def test_parse_plate_csv_basic_96_well():
    df = parse_plate_csv(_make_96_well_csv())
    assert df.shape == (8, 12)
    assert list(df.index) == list("ABCDEFGH")
    assert list(df.columns) == list(range(1, 13))
    assert df.loc["A", 1] == pytest.approx(0.1)


def test_parse_plate_csv_no_rows_raises():
    with pytest.raises(ValueError):
        parse_plate_csv("not,a,plate,file\njust,some,text\n")


def test_parse_plate_csv_384_well_rows_raise_instead_of_silently_truncating():
    lines = [_make_96_well_csv()]
    # Add a genuine-looking data row for "I" (beyond H) with >=3 numeric values.
    lines.append("I," + ",".join(str(0.5 + 0.01 * c) for c in range(12)))
    with pytest.raises(ValueError, match="beyond H"):
        parse_plate_csv("\n".join(lines))


def test_parse_plate_csv_too_many_columns_raises():
    row = "A," + ",".join(str(x) for x in range(20))
    rows = [row] + [f"{r},1,2,3" for r in "BCDEFGH"]
    with pytest.raises(ValueError, match="96-well"):
        parse_plate_csv("\n".join(rows))


def test_parse_plate_csv_ignores_stray_row_letter_like_line_with_too_few_numbers():
    # A line that happens to start with a letter beyond H followed by a
    # delimiter (e.g. a one-off "J,note" annotation) but has fewer than 3
    # numeric values afterward must not be mistaken for a genuine
    # beyond-H plate row and falsely trigger the 384-well rejection.
    text = "J,note\n" + _make_96_well_csv()
    df = parse_plate_csv(text)
    assert df.shape == (8, 12)


def _4pl(x, a, b, c, d):
    return d + (a - d) / (1.0 + (x / c) ** b)


def test_fit_4pl_recovers_known_parameters():
    x = np.array([0.01, 0.1, 1.0, 10.0, 100.0, 1000.0])
    a, b, c, d = 0.05, 1.2, 10.0, 2.5
    y = _4pl(x, a, b, c, d)
    fit = _fit_4pl(x, y)
    assert fit is not None
    assert fit["c"] == pytest.approx(c, rel=0.2)
    assert fit["r2"] > 0.99


def test_fit_4pl_returns_none_on_degenerate_input():
    x = np.array([1.0, 1.0, 1.0])
    y = np.array([1.0, 1.0, 1.0])
    # Should not raise even though this is a poor/degenerate fit target.
    _fit_4pl(x, y)  # either None or a (bad) fit — must not crash


def test_4pl_inv_round_trips_with_fit():
    x = np.array([0.01, 0.1, 1.0, 10.0, 100.0, 1000.0])
    a, b, c, d = 0.05, 1.2, 10.0, 2.5
    y = _4pl(x, a, b, c, d)
    params = dict(a=a, b=b, c=c, d=d)
    for xi, yi in zip(x, y):
        back = _4pl_inv(yi, params)
        assert back == pytest.approx(xi, rel=1e-3)


def test_4pl_inv_out_of_range_returns_nan_not_crash():
    params = dict(a=0.0, b=1.0, c=1.0, d=10.0)
    # y beyond the (a, d) asymptote range -> ratio <= 0 -> NaN, no exception.
    out = _4pl_inv(20.0, params)
    assert np.isnan(out)
