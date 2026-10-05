# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sensor Calibration Studio — importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and microplate assay data, defining calibration windows, fitting calibration curves, and exporting results/plots. Single-user lab tool, no backend/database.

There are **two independent UIs sharing the same `core/` computation layer**, and both cover all four modes (Amperometry, Solid-State, Cyclic Voltammetry, Assay):
- The **Streamlit app** (`app.py` + `modes/`): all state lives in the Streamlit session and the user's browser. See "Streamlit app architecture" below.
- A **local web app** (`web_app/`): FastAPI + plain JS + Plotly.js, localhost-only. It reuses `core/`'s pure functions and each mode's fit math directly rather than duplicating them. See "Local web app" below.

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

# Local web app (http://127.0.0.1:8000, next free port if busy)
pip install -r requirements.txt -r requirements-web.txt
python -m web_app.main
pytest tests/web                     # FastAPI TestClient tests for web_app/
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

## Local web app

`web_app/` is a second UI for the same computation layer: a FastAPI server (`web_app/main.py`, bound to 127.0.0.1 only, no auth, single user) serving a static page (`web_app/static/`) that talks to a JSON API. Its runtime deps are in `requirements-web.txt`. It never runs a Streamlit server, but it imports `modes/*.py`, which import `streamlit` at module level, so streamlit must be installed.

**Keep the Streamlit app working.** `web_app/` imports pure functions from `core/` and `modes/` (fit math, parsers, `step_detection.py`, `calibration_table.py`, PNG-export builders like `render_cal_png`/`render_assay_curve`, `parse_plate_csv`) unmodified. When logic it needs is tangled up with Streamlit widget calls (e.g. Assay's standard-curve fit and back-calculation live inline in `modes/assay.py`'s `render()`; anything calling `core/constants.py`'s `_plot_theme()` reads the Streamlit theme), reimplement it as pure code in the web-app router (see `web_app/api/assay.py`'s docstring). Don't refactor the Streamlit render functions to suit the web app. Bug fixes to shared pure functions are fine and benefit both UIs; run the full test suite after any change to `core/` or `modes/`.

**Backend (`web_app/`):**
- `session.py`: `SessionData`, an in-memory per-browser session keyed by an httponly cookie. Field names mirror `core/state.py`'s keys. `SessionStore.get_or_create` reuses an unknown-but-well-formed cookie id, so after a server restart the page's parallel requests land in one fresh session. `static/js/app.js` also pings `/api/session/ping` before any mode loads, for the same reason.
- `api/common.py`: shared helpers. `records_to_df` coerces edited table cells (non-text columns become numeric, NaN on garbage), so typos can't reach fit code as strings. `guess_channels` maps every numeric column of a plain CSV to a channel. `ExportFmt`/`ExportStyle` are `Literal`s, so a bad export format is a 422, not a matplotlib 500.
- `api/amperometry.py`, `api/solid_state.py`, `api/cyclic_voltammetry.py`, `api/assay.py`: one router per mode. Every mutating endpoint returns the mode's full `_state()`, and the frontend re-renders from it.
- `api/assay_layout.py`: pure Assay helpers with no FastAPI or session code, unit-tested in `tests/web/test_assay_layout.py`:
  - selection → standards/blank/samples assignment;
  - subject/timepoint name-list expansion (`P01-P12`, `Day 0 - Day 14 by 7`);
  - pasted layout grids, per-sample grouping, normalisation, and amount-unit conversion.

  The Assay layout stays Streamlit-compatible:
  - Standards live in `assay_std_df` (row 0 = blank, ≤3 replicate sets).
  - Samples live in `assay_sample_df`, with extra `Subject`/`Timepoint` columns Streamlit carries along; `Label` is composed from them when blank.
  - The normalisation inputs (`assay_norm`) and readout (`assay_readout`) are web-only bundle keys, which Streamlit's import ignores.
- `api/session.py`: Tier 2 Export/Import JSON with **the same keys and shape as `core/persistence.py`'s `_build_session_bundle`** (assay included, plus the web-only `assay_norm`/`assay_readout` keys), so sessions move between the two apps both ways. It reuses `_jsonify`/`_plate_df_to_csv`/`_plate_df_from_csv` and `core/calibration_table.py`'s record parsers. Import applies to a deep copy and swaps in only on success.

**Frontend (`web_app/static/`)**, no build step:
- `index.html` holds all four modes' markup, and element ids are the contract with the scripts.
- `js/app.js` holds the mode/tab switching and shared helpers: `apiCall`, `onClick` (errors become toasts), `download`, `drawPlot`, `renderChecklist` (labels never seen before start checked, so adding a file never blanks a plot), `exportOptions`, and `previewExport`. Plots drawn into a hidden tab are resized when the tab is shown.
- `js/trace_mode.js` contains `createTraceMode(cfg)`, the shared UI for Amperometry and Solid-State (the web counterpart of `core/shared_tabs.py`). `js/amperometry.js` and `js/solid_state.js` are thin configs. `js/cyclic_voltammetry.js` and `js/assay.js` are standalone. `assay.js` builds the ② Plate Layout plate as HTML (not Plotly) so wells can be drag-selected, and gets assignment previews from the server (`preview: true` on the `/assign/*` endpoints) so the labelling rules exist only in Python.

**Lifecycle (`web_app/main.py`):** single instance. The running server's port/pid goes in `~/Library/Application Support/Sensor Calibration Studio/server.json`, and a second launch detects it via `/api/app/info` and just opens the browser. `/api/app/quit` (the header's Quit button) stops uvicorn. Pages POST `/api/app/heartbeat` every 60 s, which drives the optional `WEB_APP_IDLE_SHUTDOWN_MIN` auto-exit.

**macOS app (`packaging/`):** `build_mac_app.sh` compiles `app.applescript` into a stay-open applet. It copies `web_app/`, `core/`, `modes/` and `sample_data/` plus a fresh venv (built from `requirements-web.txt`, reused while the requirements hash matches) into `Contents/Resources`, swaps in `AppIcon.icns`, and ad-hoc re-signs. The applet runs `start-server.sh` and must stay running while the server runs: macOS kills processes an app spawned, even setsid'd or bootstrapped launchd jobs, once the app process exits, so a launch-and-exit wrapper doesn't work. Its `idle` handler quits the applet when the server stops, and its `quit` handler stops the server. The bundle is self-contained so it never reads `~/Documents` (TCC-protected) at launch. Code changes need a re-run of the build script.

Tier 1 (browser localStorage settings) and Tier 3 (Google Drive) persistence, and Ollama AI Insights, are Streamlit-only.
