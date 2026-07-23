import numpy as np
import pytest

from app.analysis.logistic import fit_4pl, inv_4pl


def test_fit_4pl_recovers_known_parameters():
    a, b, c, d = 0.05, 1.2, 10.0, 3.0
    x = np.array([0.1, 1, 3, 10, 30, 100, 300])
    y = d + (a - d) / (1.0 + (x / c) ** b)
    fit = fit_4pl(x, y)
    assert fit is not None
    assert fit["r2"] > 0.999
    assert fit["c"] == pytest.approx(c, rel=0.1)


def test_inv_4pl_round_trips_with_fit():
    a, b, c, d = 0.05, 1.2, 10.0, 3.0
    p = dict(a=a, b=b, c=c, d=d)
    y_at_c = d + (a - d) / (1.0 + 1.0 ** b)   # x=c -> ratio=1
    x_back = inv_4pl(y_at_c, p)
    assert x_back == pytest.approx(c, rel=1e-3)


def test_inv_4pl_out_of_range_returns_nan():
    p = dict(a=0.0, b=1.0, c=10.0, d=1.0)
    # y == d makes the ratio division blow up / go negative -> NaN
    assert np.isnan(inv_4pl(1.0, p))
