"""Every mode boots from the registry and its main flow works in a browser."""
import numpy as np

from browser_helpers import open_mode, open_tab
from web_app.registry import MODES


def _cv_csv(scale=1.0):
    v = np.concatenate([np.linspace(-0.2, 0.6, 100), np.linspace(0.6, -0.2, 100)])
    i = scale * np.exp(-((v - 0.25) / 0.05) ** 2) * np.r_[np.ones(100), -np.ones(100)]
    return "V,I\n" + "\n".join(f"{a},{b}" for a, b in zip(v, i))


def test_nav_comes_from_the_registry(page):
    ids = page.eval_on_selector_all(".mode-btn", "els => els.map(e => e.dataset.mode)")
    assert ids == [m.id for m in MODES]
    for m in MODES:
        assert page.locator(f"#mode-{m.id} .tab-btn").count() >= 3


def test_amperometry_flow_and_report(page):
    open_mode(page, "amperometry")
    page.click("#amp-sample-btn")
    page.wait_for_selector("#amp-files-list li span")
    open_tab(page, "amperometry", "results")
    page.click("#amp-compute-btn")
    page.wait_for_function("document.getElementById('amp-cal-plot').data")
    with page.expect_popup() as pop:
        page.click("#report-btn")
    report = pop.value
    report.wait_for_load_state()
    assert "Sensor statistics" in report.content()


def test_solid_state_flow(page):
    open_mode(page, "solid_state")
    page.click("#ss-sample-btn")
    page.wait_for_selector("#ss-files-list li span")
    open_tab(page, "solid_state", "results")
    page.click("#ss-compute-btn")
    page.wait_for_function("document.getElementById('ss-cal-plot').data")


def test_cv_flow(page, tmp_path):
    open_mode(page, "cyclic_voltammetry")
    files = []
    for sr, scale in ((10, 1.0), (50, 2.0), (100, 3.0)):
        f = tmp_path / f"cv_{sr}mVs.csv"
        f.write_text(_cv_csv(scale))
        files.append(str(f))
    page.set_input_files("#cv-file-input", files)
    page.wait_for_function("cvState && cvState.runs.length === 3")
    open_tab(page, "cyclic_voltammetry", "plot")
    page.click("#cv-find-peaks-btn")
    page.wait_for_function("cvState.runs.every(r => r.n_peaks > 0)")


def test_undo_redo_buttons_and_keyboard(page):
    open_mode(page, "amperometry")
    assert page.is_disabled("#undo-btn")
    page.click("#amp-sample-btn")
    page.wait_for_selector("#amp-files-list li span")
    assert page.is_enabled("#undo-btn")
    page.keyboard.press("Meta+z")
    page.wait_for_selector("#amp-files-list li.hint")   # "No files loaded yet."
    assert page.is_enabled("#redo-btn")
    page.click("#redo-btn")
    page.wait_for_selector("#amp-files-list li span")
    # Each mode has its own history.
    open_mode(page, "assay")
    assert page.is_disabled("#undo-btn")


def test_reload_keeps_session_and_mode(page):
    open_mode(page, "assay")
    page.click("#assay-sample-btn")
    page.wait_for_function("assayState && assayState.plate !== null")
    page.reload()
    page.wait_for_function("typeof assayState !== 'undefined' && assayState && assayState.plate !== null")
    assert "active" in page.get_attribute('.mode-btn[data-mode="assay"]', "class")
