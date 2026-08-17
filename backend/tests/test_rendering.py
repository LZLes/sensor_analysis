import numpy as np
import pandas as pd

from app.analysis.rendering import render_cal_png, render_ts_png
from app.analysis.calibration import apply_effective_concentration


def _sample_amp_file():
    t = np.arange(0, 60, 1.0)
    df = pd.DataFrame({"Time (s)": t, "Current (uA)": 1.0 + 0.01 * t})
    cpdf = pd.DataFrame({
        "Label": ["Blank", "Step 1"],
        "Concentration": [0.0, 1.0],
        "Spike Vol": [np.nan, np.nan],
        "Stock Conc": [np.nan, np.nan],
        "t_start": [0.0, 30.0],
        "t_end": [10.0, 50.0],
        "avg_duration": [np.nan, np.nan],
        "Baseline": [True, False],
    })
    return [{
        "filename": "sample.csv", "df": df,
        "channels": [{"name": "Channel 1", "tc": "Time (s)", "ic": "Current (uA)"}],
        "cpdf": cpdf,
    }]


def test_render_ts_png_produces_png_bytes():
    amp_files = _sample_amp_file()
    png = render_ts_png(amp_files, "µA", ["Channel 1"])
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_ts_png_svg_format():
    amp_files = _sample_amp_file()
    svg = render_ts_png(amp_files, "µA", ["Channel 1"], fmt="svg")
    assert b"<svg" in svg[:200] or b"<?xml" in svg[:200]


def test_render_cal_png_with_segmented_fit():
    res_map = {
        "Channel 1": {
            "concs": [0.0, 1.0, 2.0, 3.0],
            "labels": ["Blank", "Step1", "Step2", "Step3"],
            "avgs": [1.0, 1.5, 2.5, 4.0],
            "sigs": [0.01, 0.02, 0.02, 0.03],
            "delta_i": [0.0, 0.5, 1.5, 3.0],
            "sigma_bl": 0.01,
            "is_average": False,
            "baselines": [True, False, False, False],
        }
    }
    png = render_cal_png(res_map, "Linear", 1, "mM", "µA")
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
