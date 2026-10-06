# CLAUDE.md

Guidance for working in this repo. `README.md` has end-user install/run instructions; this file covers the rules that aren't visible from the code, how to check your work, and where things live. When the code and this file disagree, trust the code and fix this file.

## What this is

Sensor Calibration Studio is a single-user lab tool. It imports electrochemical sensor data (amperometry, potentiometric/solid-state, cyclic voltammetry) and 96-well microplate assays, fits calibration curves, and exports results and plots. There is no backend database and no auth.

It has two independent UIs over one computation layer, and both cover all four modes:
- **Streamlit app**: `app.py` plus `modes/<mode>.py`. Each module has a `render()`, dispatched from `MODES` in `app.py`.
- **Local web app**: `web_app/`. FastAPI on 127.0.0.1, plain JS and Plotly.js, no build step. It runs without Streamlit installed.
- **Shared code** in `core/`. The fit maths for every mode is in `core/analysis/`, which never imports Streamlit.

## Environment and commands

The dev environment is `.venv-web/` (Python 3.13), which has everything installed: Streamlit, the web app, pytest, ruff and Playwright.

```bash
.venv-web/bin/ruff check .                     # lint (config in pyproject.toml)
.venv-web/bin/python -m pytest -q              # everything (~200 tests, ~20 s)
.venv-web/bin/python -m pytest tests/unit      # pure functions
.venv-web/bin/python -m pytest tests/web       # web app API (FastAPI TestClient)
.venv-web/bin/python -m pytest tests/browser   # real Chromium via Playwright; skipped if not installed
.venv-web/bin/python -m web_app.main           # web app on http://127.0.0.1:8000 (next free port if busy)
.venv-web/bin/python -m streamlit run app.py   # Streamlit app on :8501
./packaging/build_mac_app.sh                   # rebuild /Applications/Sensor Calibration Studio.app
```

CI (`.github/workflows/tests.yml`) runs four jobs: lint, the full suite on 3.11 and 3.13, the web tests with only `requirements-web.txt` installed (no Streamlit), and the browser tests.

## Rules, and why

1. **Sessions must move between the two apps both ways.**
   - The Export/Import JSON bundle uses the keys of `core/persistence.py`'s `_build_session_bundle`. The web app writes them from each mode's `ModeSpec.export_bundle`.
   - Web-only data goes under extra keys, which Streamlit ignores: `assay_norm`, `assay_readout`, `assay_plates`, `assay_active`.
   - Never change the shape of an existing key.
   - The assay's flat keys describe the active plate in Streamlit's schema: standards columns S1–S3 only, and `assay_std_res` set to `None` when that fit can't be expressed there (more than 3 replicates, or a borrowed curve).
   - Tests in `tests/web/test_assay_features.py` pin this behaviour.
2. **Don't refactor the Streamlit `render()` functions to suit the web app.**
   - When the web app needs logic that is tangled with Streamlit widgets, write a pure version in `core/analysis/`.
   - Pure top-level helpers moved out of `modes/` stay importable from there through re-exports (`from core.analysis.x import (...)  # noqa: F401`), so tests and old imports keep working.
   - Bug fixes to shared pure functions are welcome. Run the full suite afterwards.
3. **`core/analysis/`, `core/serialization.py` and all of `web_app/` must not import Streamlit or `modes/`.**
   - `tests/web/test_shell.py` blocks the import and checks this.
   - `web_app/__init__.py` sets Matplotlib to `Agg`. Exports are drawn in worker threads, and the default macOS backend crashes there. Streamlit used to set this as a side effect.
4. **Business rules live in Python only.**
   - Labelling, fitting, grouping and normalisation rules are not re-implemented in JS.
   - The page asks the server, e.g. the `/assign/*` endpoints with `preview: true` return the result without saving it.
5. **Every web endpoint that changes data returns the mode's full state.** The page re-renders from that state.
   - Decorate an endpoint with `@tracked(mode_id, UNDO_FIELDS)` (`web_app/history.py`) when users would expect to undo what it does.
   - Don't decorate previews, exports or plate switching.
6. **Keep the Streamlit app working.** It has features the web app doesn't: Google Drive "Cloud Sessions", Ollama "AI Insights" and browser-localStorage settings.

## Architecture

**`core/`**
- `analysis/` holds the per-mode maths: `amperometry`, `solid_state`, `cv`, `assay`, `assay_layout` and `traces`.
  - Each module holds that mode's fits, sample data and Matplotlib export builders.
  - `assay_layout.py` holds the web-only plate-layout editing, grouping and normalisation.
- `parsing.py`, `step_detection.py`, `calibration_table.py`, `numeric.py`, `plotting.py` and `constants.py` are pure helpers shared by both apps. `_plot_theme()` and `_parse_one_file()` import Streamlit lazily.
- `state.py`, `persistence.py`, `shared_tabs.py`, `drive.py` and `ai_insights.py` are Streamlit-only.
- The Streamlit app persists data in three tiers, documented in the `persistence.py` docstring: browser localStorage settings, Export/Import JSON, and Google Drive.

**`web_app/`**
- **Mode registry.**
  - `registry.py` lists the modes.
  - `modespec.py` defines `ModeSpec`: id, label, router, `state()`, bundle export/apply, `summary()`, `undo_fields`, `report()` and shared scripts.
  - Nothing outside a mode's own files names a mode. `main.py` mounts the routers from the registry, and the page builds its nav from `/api/app/modes`.
- **Session.**
  - `session.py`'s `SessionData` is in-memory and keyed by an httponly cookie. Its field names mirror `core/state.py`.
  - New modes keep their state in `session.mode_state(id, factory)`, unless it must round-trip with Streamlit.
  - `SessionStore` reuses an unknown but well-formed cookie id. After a server restart, the page's parallel requests then share one fresh session.
- **Shell modules.**
  - `history.py`: undo/redo stacks per mode. Snapshots copy the fields but share the raw imported trace frames (`"df"` keys).
  - `autosave.py`: writes `autosave.json` a few seconds after any change. On startup the previous file is rotated to `autosave-previous.json`, and an empty session is offered it to restore.
  - `report.py`: printable HTML built from `ModeSpec.report` sections.
  - `storage.py`: the data folder. `SCS_DATA_DIR` overrides it, and the tests set it.
- **`main.py`.**
  - Host/Origin checks against DNS rebinding and cross-site POSTs.
  - Middleware that adds `X-Undo: <undo>,<redo>` to every mode response and marks the session dirty for autosave.
  - Lifecycle: single instance via `server.json`, Quit, heartbeat and idle shutdown.
- **`api/`.**
  - One router per mode, prefixed `/api/<mode id>`. The id is the same everywhere: URL, DOM `mode-<id>`, `static/modes/<id>.html` and `static/js/<id>.js`.
  - `api/session.py` handles Export/Import, `api/shell.py` handles modes/undo/report/autosave, and `api/common.py` holds request coercion and export helpers.
- **Assay plates.**
  - The active plate lives in the flat `assay_*` fields, which keeps both Streamlit compatibility and the single-plate code paths.
  - `plates(session)` returns every plate with the active one refreshed.
  - Results and Normalise pool all plates.
- **Frontend (`static/`).**
  - `index.html` is only the shell.
  - `js/app.js` boots the modes: it builds the nav and sections, loads each `modes/<id>.html` fragment, then the scripts.
  - It also holds the shared helpers (`apiCall`, `onClick`, `download`, `drawPlot`, `renderChecklist`, `exportOptions`, `previewExport`) and undo/report/restore.
  - Each mode script calls `registerMode(id, {refresh})`.
  - `js/trace_mode.js` is the shared Amperometry/Solid-State UI.
  - The Assay plate in ② is HTML rather than Plotly, so wells can be drag-selected.

**`packaging/`.** `build_mac_app.sh` compiles a stay-open AppleScript applet. It bundles `web_app/`, `core/` and `sample_data/` plus a venv built from `requirements-web.txt` alone. The applet must keep running while the server runs, because macOS kills processes an app spawned once the app exits. See the comments in the script and in `app.applescript`.

## Adding a mode to the web app

1. Add the maths to `core/analysis/<id>.py` (pure, no Streamlit), with unit tests in `tests/unit/`.
2. Create `web_app/api/<id>.py` containing:
   - `router = APIRouter(prefix="/api/<id>")`;
   - a `_state(session)`;
   - bundle hooks, with keys namespaced `<id>_…`;
   - `MODE = ModeSpec(...)`.
   Add `undo_fields` and `report` if they make sense.
3. Add `web_app/static/modes/<id>.html` (the section's markup, ids prefixed with the mode) and `web_app/static/js/<id>.js`, which ends with `registerMode("<id>", {refresh})`.
4. Add one line to `web_app/registry.py`.

`tests/web/test_shell.py::test_every_registered_mode_has_its_files_and_hooks` checks the files and hooks exist. Add API tests in `tests/web/`, and a browser test if the mode has drag/paste/keyboard interactions.

## Checking your work

- Run `ruff check .` and the full `pytest` after any Python change, and always after touching `core/` or `modes/`, because both apps depend on them.
- For UI changes, run `pytest tests/browser`. Fixtures there fail a test on any console error. For layout changes, also look at a screenshot: tests don't catch visual regressions.
- After a change to the session bundle, export from the web app and import into Streamlit, and the reverse. A quick check: load the bundle through Streamlit's `AppTest` with `_apply_session_bundle` and render the mode.
- To update the installed Mac app, re-run `./packaging/build_mac_app.sh`. The installed app runs its own copy of the code, so a dev server started while it's running just opens the app's tab.

## Gotchas

- **AppTest scripts:** `AppTest.from_function` scripts are run from their source text, so every import must be inside the function. Ruff's F811 auto-fix deletes those inner imports. Remove the duplicate module-level import instead.
- **AppTest and `app.py`:** `app.py` hangs under AppTest because of the localStorage component, so the e2e tests drive `modes.<x>.render()` directly. `tests/conftest.py` also patches an AppTest segmented-control bug.
- **CSS `[hidden]`:** the global `[hidden] { display: none !important }` rule is load-bearing. Many components set `display: flex`, which would otherwise override `hidden`.
- **Plotly in hidden tabs:** Plotly sizes charts at draw time, so charts drawn into a hidden tab are resized when the tab is shown (`resizePlotsIn`).
- **Running pytest:** use `python -m pytest` so the repo root is on `sys.path`. `tests/` has no `__init__.py`, and browser helpers are imported as `browser_helpers`.

## Git

Work on a feature branch. Write prose commit messages that explain why. Don't push or open PRs unless asked. Secrets (`.streamlit/secrets.toml`) are gitignored; only the `.example` file is committed.
