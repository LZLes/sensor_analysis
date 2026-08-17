import numpy as np
import pandas as pd
import pytest

from app.analysis.calibration import apply_effective_concentration, eff_t_start


def test_eff_t_start_uses_avg_duration_when_set():
    row = pd.Series({"t_start": 10.0, "t_end": 100.0, "avg_duration": 20.0})
    assert eff_t_start(row) == pytest.approx(80.0)


def test_eff_t_start_falls_back_to_t_start():
    row = pd.Series({"t_start": 10.0, "t_end": 100.0, "avg_duration": np.nan})
    assert eff_t_start(row) == pytest.approx(10.0)


def test_eff_t_start_none_when_nothing_set():
    row = pd.Series({"t_start": np.nan, "t_end": 100.0, "avg_duration": np.nan})
    assert eff_t_start(row) is None


def test_apply_effective_concentration_noop_without_spike_data():
    df = pd.DataFrame({
        "Concentration": [0.0, 1.0, 2.0],
        "Spike Vol": [np.nan, np.nan, np.nan],
        "Stock Conc": [np.nan, np.nan, np.nan],
        "t_start": [0.0, 10.0, 20.0],
        "t_end": [5.0, 15.0, 25.0],
        "avg_duration": [np.nan, np.nan, np.nan],
    })
    out = apply_effective_concentration(df, initial_volume=1.0)
    assert list(out["Concentration"]) == [0.0, 1.0, 2.0]


def test_apply_effective_concentration_serial_dilution():
    # 1 mL initial volume, spiking 0.1 mL of 100 mM stock twice:
    # step1: vol=1.1, mass=0.1*100=10 -> conc=10/1.1=9.0909
    # step2: vol=1.2, mass=10+10=20   -> conc=20/1.2=16.667
    df = pd.DataFrame({
        "Concentration": [0.0, 0.0, 0.0],
        "Spike Vol": [np.nan, 0.1, 0.1],
        "Stock Conc": [np.nan, 100.0, 100.0],
        "t_start": [0.0, np.nan, np.nan],
        "t_end": [5.0, 15.0, 25.0],
        "avg_duration": [np.nan, np.nan, np.nan],
    })
    out = apply_effective_concentration(df, initial_volume=1.0)
    assert out["Concentration"].iloc[0] == pytest.approx(0.0)
    assert out["Concentration"].iloc[1] == pytest.approx(10 / 1.1, rel=1e-6)
    assert out["Concentration"].iloc[2] == pytest.approx(20 / 1.2, rel=1e-6)


def test_apply_effective_concentration_derives_t_start_from_avg_duration():
    df = pd.DataFrame({
        "Concentration": [1.0],
        "Spike Vol": [np.nan],
        "Stock Conc": [np.nan],
        "t_start": [0.0],
        "t_end": [100.0],
        "avg_duration": [30.0],
    })
    out = apply_effective_concentration(df, initial_volume=1.0)
    assert out["t_start"].iloc[0] == pytest.approx(70.0)
