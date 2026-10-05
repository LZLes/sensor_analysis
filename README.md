# Sensor Calibration Studio

A lab tool for importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and 96-well microplate assay data, defining calibration windows, fitting calibration curves, and exporting results and publication-quality plots.

There are **two UIs** built on the same computation core (`core/` + the fit math in `modes/`), and both cover all four modes:

- **Streamlit app** (`app.py`): runs in a browser via `streamlit run`. It has the optional Google Drive "Cloud Sessions" and local-Ollama "AI Insights" extras.
- **Local web app** (`web_app/`): a FastAPI + Plotly app that runs a small localhost-only server and opens in your default browser. It is snappier than Streamlit for large files. It also adds drag-and-drop import, per-file remove and live export previews. For assays it adds pasting a plate straight from Excel, a selectable plate for labelling standards and subject/timepoint samples, per-sample summaries, and normalisation by area.

Both read and write the same **Export/Import Session** JSON, so a session saved in one opens in the other.

For architecture/contributor notes, see [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

---

## Local web app

Requires Python 3.10+.

```bash
python -m venv .venv-web
source .venv-web/bin/activate
pip install -r requirements.txt -r requirements-web.txt
python -m web_app.main
```

This starts a server on `http://127.0.0.1:8000` and opens it in your browser. If port 8000 is busy, the next free port is used and printed in the terminal. Everything runs on your machine: there is no remote server and no data leaves it. Stop the server with `Ctrl-C`.

Options (environment variables):

| Variable | Effect |
|---|---|
| `WEB_APP_PORT=8123` | Preferred port |
| `WEB_APP_NO_BROWSER=1` | Don't open a browser tab automatically |
| `WEB_APP_IDLE_SHUTDOWN_MIN=15` | Exit after this many minutes with no open tab (the Mac app sets it) |

Running it again while it's already running just opens a new tab on the existing server.

### Installing it as a Mac app

To open it from your Applications folder, Launchpad or Spotlight like any other app:

```bash
./packaging/build_mac_app.sh          # builds and installs /Applications/Sensor Calibration Studio.app
```

The app is self-contained: it holds its own copy of the code and its own Python environment, about 600 MB. Moving or deleting this repo folder doesn't affect it. It's built against this Mac's Python, so build it on each Mac where you want it rather than copying the `.app` across. The first build takes a few minutes. After you change the code, run the script again to update the app; this takes seconds, because the Python environment is reused.

Using it:
- **Opening the app** starts the server and opens a browser tab. While it runs it shows in the Dock. Opening it again, or clicking its Dock icon, opens a fresh tab on the same session.
- **To stop it**, press ⌘Q on the app, or use the **Quit** button in the page header. It also stops itself 15 minutes after the last tab is closed. Use **Export session** first if you want to keep your work, because data is held in memory only.
- **Logs** are in `~/Library/Logs/Sensor Calibration Studio.log`. To uninstall, drag the app to the Bin.

### Modes

- **Amperometry / Solid-State.** ① Import (browse, drag-and-drop, or sample data) → ② Time Series & Windows (plot, pick channels, edit or auto-detect calibration windows, smoothing, channel mapping; Amperometry also has the serial-dilution calculator) → ③ Calibration (linear/segmented fits with sensitivity, R², LOD, LOQ; Nernstian fit for Solid-State) → ④ Export (PNG/SVG/PDF/TIFF with DPI, style, and size options, plus a live preview) → ⑤ Compare Files.
- **Cyclic Voltammetry.** ① Import (one file per scan rate, guessed from the file name and editable) → ② Plot & Peaks → ③ Scan Rate Analysis (Ip vs ν, Randles–Ševčík, Ep, ΔEp) → ④ Export.
- **Assay (Microplate).** Colourimetric, fluorescent or luminescent; set the readout and wavelength(s) on ①, and they're recorded in the exports.
  - **① Import.** Paste the 8×12 block from Excel (⌘V anywhere on the tab), drop a plate-reader file, or type values in.
  - **② Plate Layout.** Select wells on the plate by clicking, dragging, shift-clicking, or clicking a row or column header, then assign them:
    - **Standards:** a serial dilution (top concentration and factor) or a typed list, with up to 3 replicates per level.
    - **Blank:** subtracted from every well.
    - **Samples:** subjects × timepoints, e.g. `P01-P12` and `D0, D3, D7`, with a replicate count and fill order. A live preview shows on the plate before you apply.

    You can also paste a whole layout from Excel (`Blank`, `Std 10`, `P01_D7`, …) or edit the layout as tables.
  - **③ Standard Curve.** Linear, Quadratic or 4PL.
  - **④ Results.** Mean ± SD and CV per subject and timepoint, with a time-course chart, plus per-well concentrations with out-of-range flags.
  - **⑤ Normalise & Export.** Per sample, enter dilution, volume and area:
    - sample conc = well conc × dilution
    - amount = sample conc × volume, with units worked out (e.g. µM × µL → pmol)
    - per area = amount ÷ area

    Exports cover the summary CSV (long, or subjects × timepoints for Prism/Excel), normalised, per-well and standard-curve CSVs, and the curve image.

Use **Export session** / **Import session** in the header to save your work. The server keeps sessions in memory only, so they are cleared when it stops.

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
pytest              # everything
pytest tests/unit   # fast, pure-function tests
pytest tests/web    # web app API tests (FastAPI TestClient)
```

---

## Repository layout

```
app.py, modes/        # Streamlit app (one module per mode)
core/                 # Shared parsing, fitting, plotting, persistence helpers
web_app/              # Local FastAPI + Plotly web app
packaging/            # build_mac_app.sh: wraps web_app/ as a macOS .app
  api/                #   one router per mode + session export/import
  static/             #   index.html, css/, js/ (one script per mode)
tests/                # pytest: unit/, e2e/ + regression/ (Streamlit), web/ (web app)
sample_data/          # Example data for every mode's "Load sample" button
```
