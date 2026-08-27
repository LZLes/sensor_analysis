# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sensor Calibration Studio — a Streamlit app for importing multi-channel electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and microplate assay data, defining calibration windows, fitting calibration curves, and exporting results/plots. Single-user lab tool, no backend/database — all state lives in the Streamlit session and the user's browser.

## Commands

```bash
pip install -r requirements.txt
streamlit run app.py                 # runs on localhost:8501
```

There is no test suite, linter, or build step configured in this repo. There is no `README.md`.

The devcontainer (`.devcontainer/devcontainer.json`) runs the same `streamlit run` command with CORS/XSRF disabled for Codespaces preview.

## Architecture

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
