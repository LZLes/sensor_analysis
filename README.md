# Sensor Calibration Studio

A lab tool for importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and microplate assay data, defining calibration windows, fitting calibration curves, and exporting results/plots.

There are **three independent UIs** built on the same computation core:

- **Streamlit app** (`app.py`) — runs in a browser, zero install beyond Python. Covers all four modes (Amperometry, Solid-State, Cyclic Voltammetry, Assay). Best for quick use on any machine.
- **Native macOS app** (`macos_app/`) — a PySide6/Qt desktop app with multi-window sessions, undo/redo, drag-and-drop import, publication-quality export with format/DPI/style options, and a cross-file comparison view. Covers Amperometry, Solid-State, and Cyclic Voltammetry (Assay is Streamlit-only). Best for regular day-to-day use on a Mac.
- **Local web app** (`web_app/`) — a FastAPI + Plotly app that runs a small localhost-only server and opens in your default browser. Covers Amperometry, Solid-State, and Cyclic Voltammetry (Assay is Streamlit-only). No auth, binds to `127.0.0.1` only — a lighter-weight alternative to the Streamlit app, not a hosted/multi-user service.

All three read/write the same Export/Import JSON session format, so a session saved from one opens in either of the others (Assay data round-trips through the Streamlit app only, since neither the macOS nor web app supports that mode).

This file covers installing and running all three. For architecture/contributor notes, see [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

---

## Streamlit app

Requires Python 3.10+.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`. All data stays local to your browser session — there is no backend/database. Optional features (Google Drive "Cloud Sessions", local-Ollama "AI Insights") are disabled unless configured; see `.streamlit/secrets.toml.example`.

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest              # everything
pytest tests/unit   # fast, no browser involved
```

---

## macOS app

### Run from source (any platform with Python 3.10+, for development)

```bash
pip install -r macos_app/requirements-macos.txt
python -m macos_app.main
```

This launches the Qt app directly — no packaging step needed for day-to-day development.

### Using the app

Amperometry and Solid-State each follow the same 3-step pipeline, one tab per step:

1. **① Import** — browse for files, drag-and-drop them onto the window, or load the built-in sample data. Channel names/columns are auto-detected; there's no separate "apply" step before you can see the trace. Selecting a file shows its raw parsed CSV in a table alongside the loaded-files list.
2. **② Time Series & Calibration** — the trace is plotted immediately, with a checklist to choose which channels are visible (this same checklist is what "Compute Calibration" analyses below). Fine-tune channel assignment, mark calibration windows (spike start/end, concentration, spike volume), and auto-detect step edges from the trace, in collapsible side panels next to the plot — Amperometry also has the effective-concentration (serial dilution) calculator here. Below that, in the same tab: pick a fit type, click Compute Calibration, and see the fitted curve with sensitivity/R²/LOD/LOQ statistics.
3. **③ Export** — CSV summary, plus a calibration-curve/time-series image export with a Format (PNG/SVG/PDF/TIFF), DPI, Style (Default/Origin/Minimal), and figure-size dialog, and a live preview of the exported image that updates as you change those options.

A 4th **④ Compare Files** tab overlays fits from multiple loaded files. Cyclic Voltammetry follows a similar shape (① Import → ② Plot & Peaks → ③ Scan Rate Analysis → ④ Export) without the calibration-window concept, since peak detection there plays the same role.

Every plot in the app is a native chart (scroll to zoom, drag to pan, hover for a crosshair readout) rather than an embedded web view.

### Building an installable `.app` / `.dmg`

Packaging **must be done on an actual Mac** — PyInstaller does not cross-compile, and this can't be built from Linux/Windows.

1. Install the runtime and build dependencies:

   ```bash
   pip install -r macos_app/requirements-macos.txt -r macos_app/packaging/requirements-build.txt
   ```

2. Run the build script from the repo root:

   ```bash
   ./macos_app/packaging/build_macos.sh
   ```

   This runs PyInstaller, strips unused Qt plugin categories the PySide6 hooks bundle unconditionally (SQL drivers, Qt3D, Bluetooth, multimedia, QML tooling, etc. — this app uses none of them), ad-hoc code-signs the bundle, and packages it as a `.dmg`. Output lands in `dist/`:
   - `dist/SensorCalibrationStudio.app`
   - `dist/SensorCalibrationStudio-<version>.dmg`

   The script prints bundle size before/after the plugin cleanup and after signing, so you can sanity-check the result.

### Installing the built app

1. Open the `.dmg` (double-click it in Finder).
2. Drag **Sensor Calibration Studio** into the **Applications** shortcut shown in the window.
3. Eject the `.dmg` and launch the app from `/Applications`.

### Gatekeeper warning on first launch

The app is **ad-hoc signed**, not signed with a paid Apple Developer ID and not notarized (that requires a $99/yr Apple Developer Program membership). Ad-hoc signing satisfies Apple Silicon's requirement that every executable be signed, so the app runs — but macOS Gatekeeper will still flag it as being from an "unidentified developer" the first time you open it. This is expected; do one of:

- **Right-click (or Control-click) the app → Open → Open**, in the dialog that appears, *or*
- Try to open it normally, then go to **System Settings → Privacy & Security**, scroll to the blocked-app notice near the bottom, and click **Open Anyway**.

This is a one-time step per machine. There is no way to avoid it without a paid Apple Developer account and notarization — if that's ever added, `macos_app/packaging/build_macos.sh`'s header comment documents the upgrade path (replace the ad-hoc `codesign` call with a Developer ID identity, add `notarytool submit` + `stapler staple` steps).

### Checking for updates

The app checks GitHub Releases for this repo on startup and shows a dialog if a newer version is tagged (Help → Check for Updates to check manually). This is a notification only, not an auto-updater — downloading and installing a new version still means repeating the steps above.

---

## Web app

Requires Python 3.10+. Runs on any platform (not Mac-only, unlike the packaged macOS app).

```bash
pip install -r requirements.txt -r requirements-web.txt
python -m web_app.main
```

Starts a Uvicorn server on `http://127.0.0.1:8000` and opens it in your default browser automatically (set `WEB_APP_NO_BROWSER=1` to skip that and open the URL yourself). Everything runs locally on your machine — there's no remote server and no data leaves it.

Amperometry, Solid-State, and Cyclic Voltammetry each get their own tab in the page, covering the same import → time-series/calibration → export flow as the macOS app. Use the **Export Session** / **Import Session** buttons in the header to move a session between this app, the Streamlit app, and the macOS app.

To stop the server, `Ctrl-C` the terminal it's running in (or `lsof -ti:8000 | xargs kill` if it's running in the background).

---

## Repository layout

```
app.py, modes/, core/     # Streamlit app + shared computation core
macos_app/                 # Native macOS app (PySide6/Qt), see macos_app section above
web_app/                    # Local FastAPI + Plotly web app, see web_app section above
tests/                      # pytest suite (unit/e2e/regression) covering core/ and modes/
sample_data/                 # Example CSVs for trying out each mode
```

See [`.claude/CLAUDE.md`](.claude/CLAUDE.md) for the full architecture breakdown of both UIs.
