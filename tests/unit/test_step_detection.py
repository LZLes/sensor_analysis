import numpy as np

from core.step_detection import detect_step_edges, edges_to_windows


def test_detect_step_edges_finds_clear_steps():
    t = np.arange(0, 300, 1.0)
    signal = np.zeros_like(t)
    signal[t >= 100] += 5.0
    signal[t >= 200] += 5.0
    rng = np.random.default_rng(0)
    signal += 0.01 * rng.standard_normal(t.size)

    edges = detect_step_edges(t, signal, min_step_seconds=10)
    assert len(edges) == 2
    assert abs(edges[0] - 100) <= 2
    assert abs(edges[1] - 200) <= 2


def test_detect_step_edges_flat_trace_returns_empty():
    t = np.arange(0, 100, 1.0)
    signal = np.ones_like(t)
    assert detect_step_edges(t, signal) == []


def test_detect_step_edges_too_few_points_returns_empty():
    t = np.array([0.0, 1.0, 2.0])
    signal = np.array([0.0, 5.0, 0.0])
    assert detect_step_edges(t, signal) == []


def test_detect_step_edges_respects_max_edges():
    t = np.arange(0, 400, 1.0)
    signal = np.zeros_like(t)
    for edge in (100, 200, 300):
        signal[t >= edge] += 5.0
    edges = detect_step_edges(t, signal, min_step_seconds=10, max_edges=1)
    assert len(edges) == 1


def test_detect_step_edges_handles_nan_and_unsorted_input():
    t = np.array([3.0, 1.0, np.nan, 2.0, 0.0])
    signal = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
    # Should not raise despite NaN and out-of-order t.
    assert detect_step_edges(t, signal) == []


def test_edges_to_windows_basic():
    windows = edges_to_windows([100.0, 200.0], trace_end=300.0)
    assert windows == [
        ("Step 1", 100.0, 200.0),
        ("Step 2", 200.0, 300.0),
    ]


def test_edges_to_windows_with_leading_baseline():
    windows = edges_to_windows([100.0], trace_end=200.0, include_leading_baseline=True)
    assert windows == [
        ("Baseline", 0.0, 100.0),
        ("Step 1", 100.0, 200.0),
    ]


def test_edges_to_windows_empty_input():
    assert edges_to_windows([], trace_end=100.0) == []
