# Sensor Calibration Studio

A lab tool for importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and 96-well microplate assay data, defining calibration windows, fitting calibration curves, and exporting results and publication-quality plots.

There are **two UIs** built on the same computation core (`core/`, with the fit maths in `core/analysis/`), and both cover all four modes:

- **Streamlit app** (`app.py`): runs in a browser via `streamlit run`. It has the optional Google Drive "Cloud Sessions" and local-Ollama "AI Insights" extras.
- **Local web app** (`web_app/`): a FastAPI + Plotly app that runs a small localhost-only server and opens in your default browser. It is snappier than Streamlit for large files, and it doesn't need Streamlit installed. It also adds drag-and-drop import, per-file remove, live export previews, undo/redo, autosave and printable reports. For assays it adds pasting a plate straight from Excel, a selectable plate for labelling standards and subject/timepoint samples, excluding outlier wells, LOD/LOQ and recovery QC, several plates per experiment, layout templates, per-sample summaries, and normalisation by area.

Both read and write the same **Export/Import Session** JSON, so a session saved in one opens in the other.

For architecture/contributor notes, see [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

---

## Local web app

Requires Python 3.10+.

```bash
python -m venv .venv-web
source .venv-web/bin/activate
pip install -r requirements-web.txt
python -m web_app.main
```

This starts a server on `http://127.0.0.1:8000` and opens it in your browser. If port 8000 is busy, the next free port is used and printed in the terminal. Everything runs on your machine: there is no remote server and no data leaves it. Stop the server with `Ctrl-C`.

Options (environment variables):

| Variable | Effect |
|---|---|
| `WEB_APP_PORT=8123` | Preferred port |
| `WEB_APP_NO_BROWSER=1` | Don't open a browser tab automatically |
| `WEB_APP_IDLE_SHUTDOWN_MIN=15` | Exit after this many minutes with no open tab (the Mac app sets it) |
| `SCS_DATA_DIR=/path` | Where autosaves and assay templates are kept (default `~/Library/Application Support/Sensor Calibration Studio` on macOS) |

Running it again while it's already running just opens a new tab on the existing server.

### Installing it as a Mac app

To open it from your Applications folder, Launchpad or Spotlight like any other app:

```bash
./packaging/build_mac_app.sh          # builds and installs /Applications/Sensor Calibration Studio.app
```

The app is self-contained: it holds its own copy of the code and its own Python environment (without Streamlit). Moving or deleting this repo folder doesn't affect it. It's built against this Mac's Python, so build it on each Mac where you want it rather than copying the `.app` across. The first build takes a few minutes. After you change the code, run the script again to update the app; this takes seconds, because the Python environment is reused.

Using it:
- **Opening the app** starts the server and opens a browser tab. While it runs it shows in the Dock. Opening it again, or clicking its Dock icon, opens a fresh tab on the same session.
- **To stop it**, press ⌘Q on the app, or use the **Quit** button in the page header. It also stops itself 15 minutes after the last tab is closed. Your work is autosaved, and the next launch offers to restore it.
- **Logs** are in `~/Library/Logs/Sensor Calibration Studio.log`. To uninstall, drag the app to the Bin.

### Modes

- **Amperometry / Solid-State.** ① Import (browse, drag-and-drop, or sample data) → ② Time Series & Windows (plot, pick channels, edit or auto-detect calibration windows, smoothing, channel mapping; Amperometry also has the serial-dilution calculator) → ③ Calibration (linear/segmented fits with sensitivity, R², LOD, LOQ; Nernstian fit for Solid-State) → ④ Export (PNG/SVG/PDF/TIFF with DPI, style, and size options, plus a live preview) → ⑤ Compare Files.
- **Cyclic Voltammetry.** ① Import (one file per scan rate, guessed from the file name and editable) → ② Plot & Peaks → ③ Scan Rate Analysis (Ip vs ν, Randles–Ševčík, Ep, ΔEp) → ④ Export.
- **Assay (Microplate).** Colourimetric, fluorescent or luminescent; set the readout and wavelength(s) on ①, and they're recorded in the exports. An experiment can hold **several plates** (the bar above the tabs): ①–③ work on the selected plate, and ④–⑤ pool all of them by subject and timepoint. A plate without its own standards can use another plate's curve.
  - **① Import.** Paste the 8×12 block from Excel (⌘V anywhere on the tab), drop a plate-reader file, or type values in.
  - **② Plate Layout.** Select wells on the plate by clicking, dragging, shift-clicking, or clicking a row or column header, then assign them:
    - **Standards:** a serial dilution (top concentration and factor) or a typed list, with up to 12 replicates per level (the Streamlit app reads the first 3).
    - **Blank:** subtracted from every well. With 2 or more blank wells you also get LOD/LOQ.
    - **Samples:** subjects × timepoints, e.g. `P01-P12` and `D0, D3, D7`, with a replicate count and fill order. A live preview shows on the plate before you apply.

    - **Exclude:** leave outlier wells out of every calculation (curve, blank, sample means). They stay on the plate, struck through, and can be put back.

    You can also paste a whole layout from Excel (`Blank`, `Std 10`, `P01_D7`, …), edit the layout as tables, or save it as a **template** for the next run.
  - **③ Standard Curve.** Linear, Quadratic or 4PL, with QC: LOD (3 × blank SD) and LOQ (10 × blank SD) read off the curve, and back-calculated recovery per standard (flagged outside 80–120%).
  - **④ Results.** Mean ± SD and CV per subject and timepoint, with a time-course chart, plus per-well concentrations flagged when outside the standard range or below the LOD/LOQ. Untick a well to exclude it.
  - **⑤ Normalise & Export.** Per sample, enter dilution, volume and area:
    - sample conc = well conc × dilution
    - amount = sample conc × volume, with units worked out (e.g. µM × µL → pmol)
    - per area = amount ÷ area

    Exports cover the summary CSV (long, or subjects × timepoints for Prism/Excel), normalised, per-well and standard-curve CSVs, and the curve image.

In the header, for every mode:
- **↶ / ↷** (⌘Z / ⇧⌘Z) undo and redo changes to the current mode's data, such as layout assignments, table edits and removed files.
- **Report** opens a printable page (curve, fit statistics, tables) to print or save as PDF.
- **Export session** / **Import session** save everything to one JSON file you can keep or share, which also opens in the Streamlit app.

The session is autosaved to the data folder a few seconds after each change. After a restart, a fresh page offers to restore it.

---

## Streamlit app

```bash
pip install -r requirements.txt
streamlit run app.py
```

This opens at `http://localhost:8501`. Optional features (Google Drive "Cloud Sessions", local-Ollama "AI Insights") stay disabled unless configured; see `.streamlit/secrets.toml.example`.

---

## Running the tests

```bash
pip install -r requirements-dev.txt
ruff check .        # lint
pytest              # everything
pytest tests/unit   # fast, pure-function tests
pytest tests/web    # web app API tests (FastAPI TestClient)

# Real-browser tests of the web app (skipped unless Playwright is installed):
pip install playwright && python -m playwright install chromium
pytest tests/browser
```

GitHub Actions runs lint, the full suite, the web app on its own requirements (no Streamlit), and the browser tests on every push (`.github/workflows/tests.yml`).

---

## Repository layout

```
app.py, modes/        # Streamlit app (one module per mode)
core/                 # Shared parsing, plotting, persistence helpers
  analysis/           #   the fit maths for every mode (no Streamlit), used by both UIs
web_app/              # Local FastAPI + Plotly web app
  registry.py         #   the list of modes it serves
  api/                #   one router per mode, plus session export/import and shell endpoints
  static/             #   index.html shell, modes/<id>.html, js/<id>.js, css/
packaging/            # build_mac_app.sh: wraps web_app/ as a macOS .app
tests/                # pytest: unit/, e2e/ + regression/ (Streamlit), web/ (web app API), browser/ (Playwright)
sample_data/          # Example data for every mode's "Load sample" button
```

To add a new analysis mode to the web app, see "Adding a mode" in [`.claude/CLAUDE.md`](.claude/CLAUDE.md).
