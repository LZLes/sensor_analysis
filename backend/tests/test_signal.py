import numpy as np

from app.analysis.signal import smooth_signal


def test_smooth_signal_none_passthrough():
    arr = np.array([1.0, 5.0, 2.0, 8.0])
    out = smooth_signal(arr, "None", 5)
    assert np.array_equal(out, arr)


def test_smooth_signal_moving_average_reduces_noise():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 200)
    clean = np.sin(x)
    noisy = clean + rng.normal(0, 0.3, size=x.shape)
    smoothed = smooth_signal(noisy, "Moving average", 11)
    assert np.std(smoothed - clean) < np.std(noisy - clean)
    assert smoothed.shape == noisy.shape


def test_smooth_signal_savitzky_golay_reduces_noise():
    rng = np.random.default_rng(1)
    x = np.linspace(0, 10, 200)
    clean = np.sin(x)
    noisy = clean + rng.normal(0, 0.3, size=x.shape)
    smoothed = smooth_signal(noisy, "Savitzky-Golay", 11, 2)
    assert np.std(smoothed - clean) < np.std(noisy - clean)


def test_smooth_signal_empty_array():
    out = smooth_signal(np.array([]), "Moving average", 5)
    assert out.size == 0


def test_smooth_signal_window_larger_than_array_does_not_crash():
    arr = np.array([1.0, 2.0, 3.0])
    out = smooth_signal(arr, "Savitzky-Golay", 51, 2)
    assert out.shape == arr.shape
