# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sensor Calibration Studio — importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and microplate assay data, defining calibration windows, fitting calibration curves, and exporting results/plots. Single-user lab tool, no backend/database.

There are **two independent UIs sharing the same `core/` computation layer**:
- The original **Streamlit app** (`app.py` + `modes/`) — all state lives in the Streamlit session and the user's browser. See "Streamlit app architecture" below.
- A **native macOS app** (`macos_app/`) — a PySide6/Qt desktop UI built later, reusing `core/`'s pure functions and each mode's fit math directly rather than duplicating it. See "Native macOS app" below. **Never edit `app.py`, `modes/*.py`, or existing behavior of any `core/*.py` function to support the macOS app** — that UI is additive-only against this codebase; see its own section for how new shared logic gets added instead.

See `README.md` for end-user install/run instructions for both. This file is architecture guidance for working in the code.

## Commands

```bash
# Streamlit app
pip install -r requirements.txt
streamlit run app.py                 # runs on localhost:8501

# Tests (exercise core/ and modes/ via Streamlit's AppTest harness — see tests/conftest.py)
pip install -r requirements-dev.txt
pytest tests/unit                    # fast, no browser/AppTest involved
pytest tests/e2e tests/regression    # AppTest-driven, slower
pytest                               # everything

# macOS app (see README.md for the full build/package/install flow)
pip install -r macos_app/requirements-macos.txt
python -m macos_app.main
```

There is no linter or build step configured for the Streamlit app.

The devcontainer (`.devcontainer/devcontainer.json`) runs the same `streamlit run` command with CORS/XSRF disabled for Codespaces preview.

## Streamlit app architecture

**Entry point:** [app.py](app.py) only handles page chrome shared across all modes — sidebar (mode switcher + the three persistence mechanisms below) and dispatch to whichever mode is selected in `SS.mode`. All actual feature code lives in `core/` (shared infrastructure) and `modes/` (one file per analysis mode, each with a `render()` entry point that `app.py` calls).

**The four modes** (`modes/amperometry.py`, `modes/solid_state.py`, `modes/cyclic_voltammetry.py`, `modes/assay.py`) are largely independent verticals — each owns its own tabs, calibration-table schema, and fit math. `amperometry.py` and `solid_state.py` are the most similar (both are trace-based, time-windowed calibrations) and share their Import/Time-Series tab code via `core/shared_tabs.py`; don't duplicate logic between them that could live there instead. Key differences called out in their module docstrings: Amperometry does baseline subtraction, segmented-linear fits, and has the effective-concentration dilution calculator; Solid-State does Nernstian (E vs log-concentration) fits and has neither. `cyclic_voltammetry.py` and `assay.py` (4PL microplate curves) are standalone.

**`core/` modules and their roles:**
- `state.py` — single flat dict of session-state defaults for *all* modes, initialized once at startup. Centralized deliberately so switching `SS.mode` never `KeyError`s on a key only another mode's file defines.
- `parsing.py` — file ingestion: standard CSV, multi-channel potentiostat exports (Bio-Logic/CH Instruments-style, with metadata/channel-label/units header rows), and PalmSens `.pssession` (zipped XML, tries 3 layouts).
- `step_detection.py` — pure/mode-agnostic derivative-based edge detection to auto-suggest calibration window boundaries from a trace, instead of the user reading start/end times off the chart by eye.
- `calibration_table.py` — the amperometry calibration-table (`cpdf`) schema/builders; lives in `core/` rather than `modes/amperometry.py` specifically so `core/persistence.py` can use it without a `modes → core → modes` import cycle.
- `persistence.py` — three separate persistence tiers, each with a different payload (see below).
- `drive.py` — optional Google Drive "Cloud Sessions" backend; degrades to disabled (not a crash) if Drive libs are missing/broken or secrets aren't configured — notably catches `BaseException` on import since a broken crypto backend can raise a non-`Exception` pyo3 panic.
- `ai_insights.py` — optional local-Ollama "AI Insights" panel shared by Amperometry/Solid-State; sends only computed fit statistics, never raw trace data, to a locally-running model.
- `numeric.py`, `plotting.py`, `constants.py` — generic signal/regression helpers, shared matplotlib export presets (`origin`/`minimal`/`default` styles used across every mode's PNG export), and shared color palette/theme/formatting helpers.

**Persistence has three distinct tiers with different payloads** (see `core/persistence.py` docstring) — know which one a change affects:
1. **Save** (button in sidebar) → browser `localStorage` via `streamlit_local_storage`. Settings/units only, no raw trace data or calibration tables (keeps well under browser storage quotas). Auto-loaded on next visit to the same browser.
2. **Export/Import JSON** → full session bundle including embedded CSV text of uploaded amperometry files and their per-file calibration tables, downloadable/shareable as one file.
3. **Cloud Sessions** (optional, Google Drive) → same full bundle as #2, saved to a shared Drive folder (requires `gcp_service_account` + `gdrive_folder_id` in `.streamlit/secrets.toml`; see `.streamlit/secrets.toml.example` for setup steps).

Calibration tables are **per-file**, not shared across an upload batch — each entry in `SS.amp_files`/`SS.solid_files` carries its own `cpdf` (calibration-points DataFrame). `core/persistence.py`'s docstring notes this persistence layer deliberately hardcodes each mode's keys directly rather than a generic per-mode-hook abstraction, and that `solid_unit`/multi-file solid-state data don't fully round-trip through all three tiers yet — check current behavior before assuming symmetry with amperometry.

The plotting stack is split: **Plotly** for interactive in-app charts, **Matplotlib** (headless `Agg` backend, set at the top of `app.py` before any other import touches `pyplot`) for publication-style PNG/SVG/PDF export, via the shared rc-context presets in `core/plotting.py`.

Secrets (`.streamlit/secrets.toml`) are gitignored; only `.example` is committed.

Tests (`tests/`) exercise this code via Streamlit's `AppTest` harness rather than mocking `pandas`/`numpy`/`scipy`/`streamlit` — `tests/unit/` targets `core/`/`modes/` pure functions directly, `tests/e2e/` drives each mode's `render()` through `AppTest.from_function` (bypassing `app.py`'s sidebar/localStorage-loading code, which hangs under `AppTest` — see `tests/conftest.py`'s docstring), and `tests/regression/test_known_bugs.py` pins specific fixed bugs so they can't silently reappear.

## Native macOS app

`macos_app/` is a second, independent UI for the same computation layer — a PySide6/Qt desktop app, built after the Streamlit app existed, packaged as a standalone `.app`/`.dmg` rather than run via `streamlit run`. It has its own entry point (`macos_app/main.py`), its own dependency list (`macos_app/requirements-macos.txt`), and its own persistence model — it does not use `st.session_state`, `streamlit_local_storage`, or anything else Streamlit-specific at runtime.

**Non-negotiable constraint: `macos_app/` never changes `app.py`, `modes/*.py`, or the existing behavior of any `core/*.py` function.** The Streamlit app must keep working unmodified. When `macos_app/` needs logic that's tangled up with a Streamlit widget call in an existing file (e.g. `core/parsing.py`'s `_parse_one_file` mixes parsing with `st.selectbox`/`st.number_input`), the fix is to add a new pure sibling function alongside the existing one (see `parse_with_options` next to `_parse_one_file`) — never to refactor the original. Where a whole interactive chart is built inside a Streamlit-coupled function (e.g. anything calling `core/constants.py`'s `_plot_theme()`, which reads `st.context.theme.type`), `macos_app/` rebuilds the equivalent Plotly figure as new code using its own `macos_app/ui/theme.py` instead of importing the original — see any `ui/modes/*_view.py`'s module docstring for the specific reasoning per mode. Everything genuinely pure (fit math, parsing, `step_detection.py`, `calibration_table.py`, PNG-export builders like `render_cal_png`) is imported and reused directly, unmodified.

**Structure:**
- `macos_app/ui/app_state.py` — `AppState`, the Qt equivalent of `core/state.py`'s `SS` dict: one instance per open window (not a singleton — multi-window is supported), owns a `QUndoStack`, mutated only through `macos_app/ui/undo_commands.py`'s commands (`SetFieldCommand` for a plain field, `FilesListCommand` for replacing a whole `amp_files`/`solid_files`/`cv_runs` list — these fire different signals on undo/redo and are **not** interchangeable, see that file's docstring, `TableEditCommand` for a per-file calibration-table edit).
- `macos_app/ui/widgets/` — shared panels reused across modes the same way Streamlit's `core/shared_tabs.py` is: `import_panel.py`/`timeseries_panel.py`/`autodetect_panel.py` (Amperometry + Solid-State), `plot_view.py` (Plotly-in-`QWebEngineView`, used by every mode), `editable_table_view.py`/`pandas_table_model.py` (the `st.data_editor` replacement), `comparison_view.py` (cross-file overlay — new relative to the Streamlit app, not a port).
- `macos_app/ui/modes/*_view.py` — one per mode, mirroring `modes/*.py`'s scope. Amperometry/Solid-State reuse the shared panels above; Cyclic Voltammetry and Assay are self-contained (matching how their Streamlit counterparts don't use `core/shared_tabs.py` either).
- `macos_app/persistence.py` — Tier 2 (Export/Import JSON) only, **byte-compatible with `core/persistence.py`'s bundle shape** — a session exported from either app opens in the other. Reuses `core/persistence.py`'s `_jsonify`/`_plate_df_to_csv`/`_plate_df_from_csv` and `core/calibration_table.py`'s `_cpdf_from_records`/`_solid_cpdf_from_records` directly. Tier 1 (settings-only "Save") is `macos_app/ui/settings.py`'s `QSettings` wrapper, unrelated to browser `localStorage`. Tier 3 (Google Drive Cloud Sessions) was deliberately not built — a native app has a real filesystem, so Tier 2 already covers session sharing.
- `macos_app/update_check.py` + `macos_app/ui/update_dialogs.py` — a lightweight "new version available" notifier against this repo's GitHub Releases API (not a full auto-updater — the app is ad-hoc signed, not notarized, so Gatekeeper's warning shows on every install regardless of how the download happened; see `update_check.py`'s docstring).
- `macos_app/packaging/` — `pyinstaller.spec` + `build_macos.sh` build, ad-hoc sign, and `.dmg`-package the app. Must run on an actual Mac (PyInstaller doesn't cross-compile); see `README.md` for the full flow.

Google Drive (`core/drive.py`) and Ollama AI Insights (`core/ai_insights.py`) are deliberately **not** imported anywhere in `macos_app/` — Drive per the tier-3 decision above, AI Insights because it was never wired into a mode view. Don't add either to `macos_app/packaging/pyinstaller.spec`'s `hiddenimports` without actually building the corresponding feature first: doing so previously pulled in `google-auth`'s `cryptography` dependency and broke the PyInstaller build in the sandbox this was developed in (see the spec file's comment).
