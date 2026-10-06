"""core/analysis/traces.py: the per-window averaging both web trace modes use."""
import numpy as np
import pandas as pd

from core.analysis.traces import window_stats


def test_window_stats_mean_sd_and_override():
    t = np.arange(0, 100, 1.0)
    y = np.where(t < 50, 1.0, 3.0) + np.where(t % 2 == 0, 0.1, -0.1)
    cpdf = pd.DataFrame({
        "t_start": [0, 60, np.nan, 10], "t_end": [40, 90, 20, np.nan],
        "avg_duration": [np.nan, 10, np.nan, np.nan], "Reading_mV": [np.nan, np.nan, 7.5, np.nan],
    })
    avgs, sigs = window_stats(t, y, cpdf, override_col="Reading_mV")
    assert np.isclose(avgs[0], 1.0, atol=0.01) and sigs[0] > 0
    assert np.isclose(avgs[1], 3.0, atol=0.02)   # last 10 s of 60–90
    assert avgs[2] == 7.5 and np.isnan(sigs[2])   # manual reading wins
    assert np.isnan(avgs[3])                      # no t_end
