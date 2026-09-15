# Sensor Calibration Studio

A lab tool for importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and microplate assay data, defining calibration windows, fitting calibration curves, and exporting results/plots.

There are **two independent UIs** built on the same computation core:

- **Streamlit app** (`app.py`) — runs in a browser, zero install beyond Python. Best for quick use on any machine.
- **Native macOS app** (`macos_app/`) — a PySide6/Qt desktop app with multi-window sessions, undo/redo, drag-and-drop import, and a cross-file comparison view. Best for regular day-to-day use on a Mac.

Both read/write the same Export/Import JSON session format, so a session saved from one opens in the other.

This file covers installing and running both. For architecture/contributor notes, see [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

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

## Repository layout

```
app.py, modes/, core/     # Streamlit app + shared computation core
macos_app/                 # Native macOS app (PySide6/Qt), see macos_app section above
tests/                      # pytest suite (unit/e2e/regression) covering core/ and modes/
sample_data/                 # Example CSVs for trying out each mode
```

See [`.claude/CLAUDE.md`](.claude/CLAUDE.md) for the full architecture breakdown of both UIs.
