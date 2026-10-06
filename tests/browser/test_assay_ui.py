"""Assay mode in a real browser: paste a plate, drag-select and label wells,
fit, exclude an outlier, normalise, add a second plate, and templates."""
import os

from browser_helpers import open_mode, open_tab

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _clipboard_plate() -> str:
    lines = [ln for ln in open(os.path.join(ROOT, "sample_data", "assay_plate.csv")).read().splitlines()
             if not ln.startswith("#")]
    return "\n".join("\t".join(ln.split(",")) for ln in lines[1:])  # Excel-style, row letters, no header


def _paste(page, text, target="document.body"):
    page.evaluate("""([t, target]) => { const dt = new DataTransfer(); dt.setData('text/plain', t);
        eval(target).dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""",
                  [text, target])


def _drag(page, a, b):
    ba = page.locator(f'[data-well="{a}"]').bounding_box()
    bb = page.locator(f'[data-well="{b}"]').bounding_box()
    page.mouse.move(ba["x"] + ba["width"] / 2, ba["y"] + ba["height"] / 2)
    page.mouse.down()
    page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)


def _label_plate(page):
    """Paste the sample plate, then lay out standards A1:C8 and samples D1:H8."""
    open_mode(page, "assay")
    _paste(page, _clipboard_plate())
    page.wait_for_function("assayState && assayState.n_wells > 0")
    open_tab(page, "assay", "layout")
    page.click('.seg-btn[data-assign="standards"]')
    _drag(page, "A1", "C8")
    assert "24 wells selected (A1:C8)" in page.inner_text("#assay-sel-info")
    page.select_option("#assay-std-mode", "list")
    page.fill("#assay-std-list", "0, 1, 2, 5, 10, 20, 50, 100")
    page.wait_for_function("document.getElementById('assay-assign-preview').textContent.includes('8 level(s) × 3')")
    assert page.locator(".well.preview").count() == 24
    page.click("#assay-assign-standards-btn")
    page.wait_for_function("document.querySelector('[data-well=A1]').classList.contains('role-blank')")
    assert page.inner_text('[data-well="A8"] .w-main') == "100"

    page.click('.seg-btn[data-assign="samples"]')
    _drag(page, "D1", "H8")
    page.fill("#assay-subjects", "P01-P05")
    page.fill("#assay-timepoints", "D0, D3, D7, D14")
    page.fill("#assay-reps", "2")
    page.wait_for_function("document.getElementById('assay-assign-preview').textContent.includes('= 40 wells; 40 selected')")
    page.click("#assay-assign-samples-btn")
    page.wait_for_function("document.querySelector('[data-well=H8] .w-sub').textContent === 'D14'")
    assert page.inner_text('[data-well="D3"] .w-main') == "P01"


def _fit(page, fit="4-Parameter Logistic (4PL)"):
    open_tab(page, "assay", "curve")
    page.select_option("#assay-fit-type", fit)
    page.click("#assay-compute-btn")
    page.wait_for_function("document.getElementById('assay-equation').textContent.length > 0")


def test_paste_label_fit_and_normalise(page):
    _label_plate(page)
    # Readout switches the unit, then back.
    open_tab(page, "assay", "import")
    page.select_option("#assay-readout-type", "fluorescence")
    page.wait_for_function("document.getElementById('assay-sig-unit').value === 'RFU'")
    assert page.is_visible("#assay-readout-ex") and not page.is_visible("#assay-readout-wl")
    page.select_option("#assay-readout-type", "absorbance")
    page.wait_for_function("document.getElementById('assay-sig-unit').value === 'Abs'")

    # Selection gestures.
    open_tab(page, "assay", "layout")
    page.click('[data-well="A9"]')
    page.click('[data-well="C10"]', modifiers=["Shift"])
    assert "6 wells selected (A9:C10)" in page.inner_text("#assay-sel-info")
    page.click('.plate-head[data-col="11"]', modifiers=["Meta"])
    assert "14 wells selected" in page.inner_text("#assay-sel-info")
    page.keyboard.press("Escape")
    assert "No wells" in page.inner_text("#assay-sel-info")

    _fit(page)
    assert "4PL" in page.inner_text("#assay-equation")
    assert page.is_visible("#assay-qc") and "LOD" in page.inner_text("#assay-qc")

    open_tab(page, "assay", "results")
    page.wait_for_function("document.querySelectorAll('#assay-groups-table tr').length === 21")
    assert page.evaluate("document.getElementById('assay-groups-plot').data.length") == 5

    open_tab(page, "assay", "normalise")
    page.wait_for_function("document.querySelectorAll('#assay-norm-inputs tr').length === 21")
    page.select_option("#assay-norm-fill-col", "Volume")
    page.fill("#assay-norm-fill-val", "50")
    page.click("#assay-norm-fill-btn")
    page.locator("#assay-norm-inputs tr:nth-child(2) td:nth-child(5) input").focus()
    _paste(page, "\n".join(str(1 + i * 0.25) for i in range(20)), "document.activeElement")
    assert page.input_value("#assay-norm-inputs tr:nth-child(21) td:nth-child(5) input") == "5.75"
    page.click("#assay-norm-apply-btn")
    page.wait_for_function("document.querySelector('#assay-norm-table tr') && "
                           "document.querySelector('#assay-norm-table tr').textContent.includes('pmol/cm²')")
    with page.expect_download() as d:
        page.click("#assay-export-wide-btn")
    assert open(d.value.path()).read().startswith("Subject,D0,D3,D7,D14")


def test_exclude_outlier_from_layout_and_results(page):
    _label_plate(page)
    _fit(page, "Linear")
    eq_before = page.inner_text("#assay-equation")
    open_tab(page, "assay", "layout")
    page.click('.seg-btn[data-assign="exclude"]')
    page.click('[data-well="B5"]')
    page.click("#assay-exclude-btn")
    page.wait_for_function("document.querySelector('[data-well=B5]').classList.contains('excluded')")
    open_tab(page, "assay", "curve")
    assert page.inner_text("#assay-equation") != eq_before
    assert "excl." in page.inner_text("#assay-standards-table")

    # Untick a sample well in ④ → its group drops to n = 1.
    open_tab(page, "assay", "results")
    page.wait_for_selector("#assay-results-table input[type=checkbox]")
    row = page.locator("#assay-results-table tr", has_text="D1").first
    row.locator("input[type=checkbox]").uncheck()
    page.wait_for_function("document.querySelector('[data-well=D1]').classList.contains('excluded')")
    page.wait_for_function("[...document.querySelectorAll('#assay-groups-table tr')].some("
                           "tr => tr.textContent.includes('1 excluded'))")

    # Undo puts D1 back.
    page.click("#undo-btn")
    page.wait_for_function("!document.querySelector('[data-well=D1]').classList.contains('excluded')")


def test_second_plate_borrows_the_curve(page):
    _label_plate(page)
    _fit(page, "Linear")
    page.click("#assay-plate-add-toggle")
    page.fill("#assay-plate-add-name", "Day 2")
    page.uncheck("#assay-plate-add-copy")
    page.click("#assay-plate-add-btn")
    page.wait_for_function("assayState.plates.length === 2 && assayState.active === 1")
    assert "Day 2" in page.inner_text(".plate-chip.active")
    _paste(page, _clipboard_plate())
    page.wait_for_function("assayState.n_wells > 0")
    open_tab(page, "assay", "layout")
    page.click('.seg-btn[data-assign="samples"]')
    _drag(page, "D1", "D4")
    page.fill("#assay-subjects", "P06")
    page.fill("#assay-timepoints", "D0, D3")
    page.fill("#assay-reps", "2")
    page.wait_for_function("document.getElementById('assay-assign-preview').textContent.includes('4 selected')")
    page.click("#assay-assign-samples-btn")
    page.wait_for_function("assayState.sample_rows.length === 4")
    open_tab(page, "assay", "curve")
    assert page.is_visible("#assay-curve-source-row")
    page.select_option("#assay-curve-source", "p1")
    page.wait_for_function("assayState.curve_source === 'Plate 1'")
    assert page.is_disabled("#assay-compute-btn")
    open_tab(page, "assay", "results")
    page.wait_for_function("[...document.querySelectorAll('#assay-groups-table tr')].some(tr => tr.textContent.includes('P06'))")
    assert "Plate" in page.inner_text("#assay-results-table tr:first-child")
    # Switch back to plate 1 via its chip.
    page.click(".plate-chip:not(.active) .plate-chip-name")
    page.wait_for_function("assayState.active === 0")


def test_template_round_trip(page):
    _label_plate(page)
    open_tab(page, "assay", "layout")
    page.click("#assay-templates-details summary")
    page.fill("#assay-template-name", "Five subjects")
    page.click("#assay-template-save-btn")
    page.wait_for_function("[...document.getElementById('assay-template-select').options].some(o => o.value === 'Five subjects')")
    page.click('.seg-btn[data-assign="clear"]')
    page.click('.plate-head.corner')
    page.click("#assay-assign-clear-btn")
    page.wait_for_function("assayState.sample_rows.length === 0")
    page.click("#assay-template-apply-btn")
    page.wait_for_function("assayState.sample_rows.length === 40")
    assert page.inner_text('[data-well="H8"] .w-sub') == "D14"
