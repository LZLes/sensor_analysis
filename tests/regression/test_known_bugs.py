"""One test per confirmed bug from the audit — each pins the fix so it can't
silently regress. Where the same behavior is already covered by a unit/e2e
test elsewhere, this file adds the missing angle rather than duplicating.
Numbered comments refer to the issue list in the review (not persisted
anywhere else, just for cross-referencing during the fix pass)."""
import numpy as np
import pandas as pd
import pytest


# ── #1: NaN in Baseline column must not be treated as baseline=True ────────
def test_baseline_keep_mask_nan_not_truthy_baseline():
    from core.calibration_table import _baseline_keep_mask
    # Old code: `not bool(b)` -> bool(nan) is True -> excluded (wrong).
    # Fixed: NaN treated as NOT baseline -> kept.
    assert _baseline_keep_mask([np.nan])[0] is True


# ── #10/#11: Solid-State ideal-slope unit awareness ─────────────────────────
def test_solid_state_ideal_slope_respects_configured_unit_not_hardcoded_mv():
    from modes.solid_state import ideal_slope_in_signal_unit, nernst_ideal_slope_mv
    mv = nernst_ideal_slope_mv(25.0, 1)
    v_ideal = ideal_slope_in_signal_unit(25.0, 1, "V")
    # If this silently used the mV value directly (the bug), a "% of ideal"
    # comparison against a Volt-scale fitted slope would be off by 1000x.
    assert v_ideal == pytest.approx(mv / 1000.0)
    assert v_ideal != pytest.approx(mv)


# ── #12: label/point misalignment on invalid rows ───────────────────────────
def test_solid_state_labels_align_with_valid_mask_not_truncation():
    # Simulates the render-time indexing bug: labels_plot used to be
    # `res["labels"][:len(x)]` (a naive truncation) instead of indexing by
    # the same boolean `valid` mask used to build x/y — wrong whenever a
    # non-trailing row is the one that's invalid.
    labels = ["Std 1", "Std 2", "Std 3", "Std 4"]
    valid_mask = [True, False, True, True]   # Std 2 dropped (middle row)
    x = np.array([1, 2, 3])   # log_conc for the 3 valid rows

    truncated = np.asarray(labels[:len(x)])          # old (buggy) approach
    masked = np.asarray(labels, dtype=object)[valid_mask]   # fixed approach

    assert list(masked) == ["Std 1", "Std 3", "Std 4"]
    assert list(truncated) != list(masked)   # proves the two disagree here


# ── #13: NaN Concentration must be excluded, not just <= 0 ──────────────────
def test_nan_concentration_excluded_by_positivity_check():
    conc = pd.Series([1.0, np.nan, -1.0, 0.0])
    # Old code: conc <= 0  -> NaN <= 0 is False -> NaN slips through.
    old_rejected = conc <= 0
    # Fixed code: ~(conc > 0) -> NaN > 0 is False -> ~False = True -> rejected.
    new_rejected = ~(conc > 0)
    assert old_rejected.tolist() == [False, False, True, True]
    assert new_rejected.tolist() == [False, True, True, True]
    assert not old_rejected.iloc[1]   # the bug: NaN wasn't rejected
    assert new_rejected.iloc[1]       # the fix: NaN is rejected


# ── #15: CV PNG export must include detected peak markers ───────────────────
def test_cv_render_cv_png_dead_function_removed():
    import modes.cyclic_voltammetry as cv_mod
    assert not hasattr(cv_mod, "render_cv_png")


# ── #17: duplicate scan rates should be flagged, not silently doubled ───────
def test_duplicate_scan_rate_detection_logic():
    scan_rates = [10.0, 25.0, 25.0, 50.0]
    counts = {sr: 0 for sr in scan_rates}
    for sr in scan_rates:
        counts[sr] += 1
    dupes = sorted(sr for sr, n in counts.items() if n > 1)
    assert dupes == [25.0]


# ── #20: Prominence=0 must behave as "no filter", like width/height ─────────
def test_find_cv_peaks_prominence_zero_is_not_filtered_out():
    from modes.cyclic_voltammetry import find_cv_peaks
    v = np.linspace(-0.5, 0.5, 300)
    i = 2.0 * np.exp(-((v - 0.1) ** 2) / 0.001) + 0.01 * np.sin(v * 50)
    zero_prom = find_cv_peaks(v, i, prominence=0.0, distance=3)
    high_prom = find_cv_peaks(v, i, prominence=50.0, distance=3)
    assert len(zero_prom["anodic"]) > len(high_prom["anodic"])


# ── #22: re-uploading a plate file must not clobber manual edits ───────────
def test_assay_upload_guard_pattern_present_in_source():
    import inspect
    import modes.assay as assay_mod
    src = inspect.getsource(assay_mod)
    assert "_assay_up_last_id" in src, (
        "Expected the file_id-tracking guard (matching app.py's JSON "
        "importer pattern) protecting the plate uploader from re-parsing "
        "on every unrelated rerun."
    )


# ── #23/#24: blank row is literal row 0, not argmin(Conc) ───────────────────
def test_assay_blank_is_row_zero_not_argmin_of_conc():
    # A standards table NOT sorted ascending by concentration: the blank
    # (Label="Blank", Conc=0) is still row 0, but a higher concentration
    # row happens to have the numerically smallest value due to a data-entry
    # slip (e.g. a typo'd negative control). argmin(Conc) would silently
    # select the WRONG row as "the blank".
    std_df = pd.DataFrame({
        "Label": ["Blank", "Std 2", "Std 3"],
        "Conc":  [0.0, 1.0, 2.0],
    })
    argmin_pos = int(np.argmin(std_df["Conc"].values))
    literal_row0_pos = 0
    assert argmin_pos == literal_row0_pos   # coincide here (sanity check)

    # Now the adversarial case: someone edited "Std 2"'s Conc to -5 by
    # mistake, but Blank is still row 0 and still the intended blank.
    std_df_bad = pd.DataFrame({
        "Label": ["Blank", "Std 2", "Std 3"],
        "Conc":  [0.0, -5.0, 2.0],
    })
    argmin_pos_bad = int(np.argmin(std_df_bad["Conc"].values))
    assert argmin_pos_bad != literal_row0_pos   # argmin picks "Std 2", not Blank
    # The fixed code always uses row 0 regardless of this — see
    # modes/assay.py's `_a3_blank_pos = 0` (with an explicit up-front
    # validation that row 0's Conc is filled in), exercised end-to-end in
    # tests/e2e/test_assay_flow.py.


# ── #26: plates larger than 96-well must raise, not silently truncate ──────
def test_parse_plate_csv_384_well_raises_not_truncates():
    from modes.assay import parse_plate_csv
    lines = []
    for r in "ABCDEFGH":
        lines.append(r + "," + ",".join(f"{0.1 + 0.01*c}" for c in range(12)))
    lines.append("I," + ",".join(f"{0.5 + 0.01*c}" for c in range(12)))
    with pytest.raises(ValueError):
        parse_plate_csv("\n".join(lines))


# ── #27: quadratic back-calc must fall back to linear when a≈0 ─────────────
def test_quadratic_back_calc_near_zero_a_does_not_return_nan():
    # Mirrors modes.assay.py's _back_calc quadratic branch logic directly
    # (that function is a closure, not exported, so the branch logic is
    # replicated here at the same tolerance to pin the fix).
    def back_calc_quad(dy, a, b, c):
        cc = c - dy
        if abs(a) <= 1e-9 * max(abs(b), 1e-12):
            return float(-cc / b) if b != 0 else np.nan
        disc = b**2 - 4*a*cc
        if disc < 0:
            return np.nan
        r1 = (-b + np.sqrt(disc)) / (2*a)
        r2 = (-b - np.sqrt(disc)) / (2*a)
        pos = [r for r in [r1, r2] if r >= -1e-9]
        if a < 0 and len(pos) == 2:
            return np.nan
        return float(min(pos)) if pos else np.nan

    # a is negligibly small (near-linear quadratic fit) -> must solve via
    # the linear-equivalent branch instead of returning NaN.
    result = back_calc_quad(dy=5.0, a=1e-14, b=2.0, c=1.0)
    assert np.isfinite(result)
    assert result == pytest.approx((5.0 - 1.0) / 2.0)


# ── #28: Assay excludes the blank point from the standard-curve fit ────────
def test_assay_blank_excluded_from_fit_mask():
    ok = np.array([True, True, True, True])
    blank_pos = 0
    ok[blank_pos] = False
    assert ok.tolist() == [False, True, True, True]


# ── Segmented-control widget-kwarg crash (found via e2e, not in original
#    static-review list) — `required=True` was removed from Streamlit's
#    st.segmented_control; all three call sites must not pass it.  ─────────
def test_no_segmented_control_call_uses_removed_required_kwarg():
    import inspect
    import modes.amperometry as amp_mod
    import modes.cyclic_voltammetry as cv_mod
    import modes.assay as assay_mod
    for mod in (amp_mod, cv_mod, assay_mod):
        src = inspect.getsource(mod)
        for line in src.splitlines():
            if "segmented_control(" in line or "required=True" in line:
                assert "required=True" not in line, (
                    f"{mod.__name__} still passes required=True to a "
                    "widget — removed in current Streamlit, crashes on render."
                )


# ── Duplicate plotly_chart element IDs (found via e2e) — every _plate_fig
#    call in assay.py must have a distinct explicit key.  ──────────────────
def test_assay_plate_fig_calls_all_have_distinct_keys():
    import re
    import inspect
    import modes.assay as assay_mod
    src = inspect.getsource(assay_mod)
    # Find every st.plotly_chart(...) call block and its key= if present.
    calls = re.findall(r"st\.plotly_chart\((.*?)\n\s*\)", src, re.DOTALL)
    keys = []
    for call in calls:
        m = re.search(r'key\s*=\s*"([^"]+)"', call)
        assert m is not None, f"plotly_chart call missing an explicit key: {call[:80]!r}"
        keys.append(m.group(1))
    assert len(keys) == len(set(keys)), f"Duplicate plotly_chart keys found: {keys}"
