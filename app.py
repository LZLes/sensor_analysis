"""
Sensor Calibration Studio  ·  Streamlit app
Import multi-channel amperometric data, define calibration windows,
fit and export calibration curves with sensor statistics.
"""

import json as _json
import time

import matplotlib
matplotlib.use("Agg")   # headless backend — must be set before any module
                         # below imports matplotlib.pyplot for the first time

import streamlit as st
from streamlit_local_storage import LocalStorage

from core.drive import (
    _drive_enabled, _drive_list_sessions, _drive_save_session,
    _drive_load_session, _drive_delete_session,
)
from core.persistence import (
    _apply_cfg_dict, _build_cfg_dict, _build_session_bundle, _apply_session_bundle,
)
from core.state import init_session_state
import modes.amperometry
import modes.assay
import modes.cyclic_voltammetry
import modes.solid_state

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sensor Analysis Studio", layout="wide")
# Larger tap targets for touchscreen use — purely cosmetic, no behavior change.
st.markdown("""
<style>
div[data-testid="stButton"] button,
div[data-testid="stDownloadButton"] button,
div[data-testid="stFormSubmitButton"] button {
    min-height: 44px;
    padding-top: 0.5rem;
    padding-bottom: 0.5rem;
}
div[data-testid="stCheckbox"] label, div[data-testid="stRadio"] label {
    min-height: 28px;
    padding: 0.15rem 0;
}
div[data-baseweb="select"] { min-height: 44px; }
div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    padding: 0.3rem 0.5rem;
    margin: 0.15rem;
}
</style>
""", unsafe_allow_html=True)

# Config persists in the user's browser (localStorage), not on disk — the
# deployment filesystem is ephemeral and wipes any saved file on redeploy.
_local_storage = LocalStorage()

# One-time per-session defaults for all 4 modes (see core/state.py).
init_session_state()
SS = st.session_state

# Auto-load saved config from the browser's localStorage once per session.
# The component's value arrives asynchronously, so retry across a couple of
# reruns before giving up (e.g. first-time users with nothing saved yet).
if not SS.get("config_loaded"):
    _raw_cfg = _local_storage.getItem("sensor_config")
    if _raw_cfg:
        try:
            _apply_cfg_dict(_json.loads(_raw_cfg))
        except Exception:
            pass
        SS["config_loaded"] = True
    else:
        SS["_cfg_load_tries"] = SS.get("_cfg_load_tries", 0) + 1
        if SS["_cfg_load_tries"] >= 5:
            SS["config_loaded"] = True
        else:
            time.sleep(0.2)
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar · Save / Load configuration
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Sensor Analysis Studio")
    st.radio("Section", ["Amperometry", "Solid-State", "Cyclic Voltammetry", "Assay"], key="mode")
    st.divider()
    st.subheader("Configuration")
    st.caption(
        "Three ways to pick up where you left off: **Save** remembers your "
        "settings in this browser only. **Export/Import JSON** bundles "
        "settings + calibration table + your uploaded files into one file "
        "you keep or share. **Cloud Sessions** does the same to a shared "
        "Google Drive folder, so anyone on the team can load it."
    )

    _cfg = _build_cfg_dict()

    # ── Save to browser localStorage (settings only — no raw trace data,
    #    to stay well inside the browser's storage quota) ───────────────────
    if st.button("Save", type="primary", use_container_width=True,
                 help="Saves settings + calibration table in this browser — "
                      "auto-loads next time you open the app here. Does not "
                      "include your uploaded files; use Export/Import JSON "
                      "or Cloud Sessions for that."):
        _local_storage.setItem("sensor_config", _json.dumps(_cfg, default=str))
        SS["_cfg_saved_at"] = time.strftime("%d %b %Y  %H:%M")
        st.toast("Configuration saved.", icon="✅")

    if SS.get("_cfg_saved_at"):
        st.caption(f"Last saved: {SS['_cfg_saved_at']}")
    else:
        st.caption("No saved config yet — click Save above.")

    st.divider()

    # ── Export / Import (full session, for sharing or backup across machines) ──
    with st.expander("Export / Import JSON"):
        st.caption(
            "Includes your uploaded amperometry files, so restoring one "
            "doesn't require re-uploading the original CSVs."
        )
        st.download_button(
            "Export session as JSON",
            data=_json.dumps(_build_session_bundle(), indent=2, default=str).encode(),
            file_name="sensor_session.json",
            mime="application/json",
            use_container_width=True,
        )
        _cfg_up = st.file_uploader(
            "Import session JSON",
            type=["json"],
            key="cfg_uploader",
            help="Load a session saved on another machine or shared by a colleague.",
        )
        if _cfg_up is not None and _cfg_up.file_id != SS.get("_cfg_up_last_id"):
            # file_id changes each time a new file is chosen (even a
            # same-named re-upload) but stays constant across reruns of an
            # already-processed upload — guards against re-applying (and
            # clobbering any newer edits) on every unrelated rerun, since
            # the uploader keeps returning the same file until replaced.
            SS["_cfg_up_last_id"] = _cfg_up.file_id
            try:
                _loaded = _json.loads(_cfg_up.read())
                _apply_session_bundle(_loaded)
                _local_storage.setItem("sensor_config", _json.dumps(_build_cfg_dict(), default=str))
                SS["_cfg_saved_at"] = time.strftime("%d %b %Y  %H:%M")
                st.success("Imported.")
            except Exception as _exc:
                st.error(f"Failed: {_exc}")

    st.divider()

    # ── Cloud sessions (Google Drive) — full session incl. raw trace data ──────
    if _drive_enabled():
        with st.expander("Cloud Sessions (Google Drive)"):
            st.caption(
                "Save/load full sessions — settings, calibration table, and the "
                "raw amperometry files — to a shared Drive folder. Anyone with "
                "access to this app and that folder can pick it up."
            )
            _sess_name = st.text_input(
                "Session name", key="drive_sess_name",
                placeholder="e.g. Run 2026-07-02",
            )
            if st.button("Save to Drive", use_container_width=True,
                         disabled=not _sess_name.strip()):
                try:
                    _bundle = _build_session_bundle()
                    _drive_save_session(_sess_name.strip(),
                                         _json.dumps(_bundle, default=str).encode())
                    st.toast(f"Saved '{_sess_name.strip()}' to Drive.", icon="☁️")
                    SS.pop("_drive_sessions_cache", None)
                except Exception as _exc:
                    st.error(f"Save failed: {_exc}")

            if st.button("Refresh list", use_container_width=True):
                SS.pop("_drive_sessions_cache", None)

            try:
                if "_drive_sessions_cache" not in SS:
                    SS["_drive_sessions_cache"] = _drive_list_sessions()
                _sessions = SS["_drive_sessions_cache"]
            except Exception as _exc:
                _sessions = []
                st.error(f"Couldn't list Drive sessions: {_exc}")

            if _sessions:
                _opts = {
                    f"{s['name'][:-5]}  ·  {s['modifiedTime'][:16].replace('T', ' ')}": s
                    for s in _sessions
                }
                _pick = st.selectbox("Saved sessions", list(_opts.keys()), key="drive_sess_pick")
                _lc, _dc = st.columns(2)
                if _lc.button("Load", use_container_width=True, type="primary"):
                    try:
                        _data = _drive_load_session(_opts[_pick]["id"])
                        _apply_session_bundle(_data)
                        _local_storage.setItem("sensor_config", _json.dumps(_build_cfg_dict(), default=str))
                        SS["_cfg_saved_at"] = time.strftime("%d %b %Y  %H:%M")
                        st.success("Session loaded.")
                        st.rerun()
                    except Exception as _exc:
                        st.error(f"Load failed: {_exc}")
                if _dc.button("Delete", use_container_width=True):
                    try:
                        _drive_delete_session(_opts[_pick]["id"])
                        SS.pop("_drive_sessions_cache", None)
                        st.toast("Deleted.", icon="🗑️")
                        st.rerun()
                    except Exception as _exc:
                        st.error(f"Delete failed: {_exc}")
            else:
                st.caption("No sessions saved yet.")
    else:
        st.caption(
            "Cloud sessions unavailable — add `gcp_service_account` and "
            "`gdrive_folder_id` to secrets to enable saving to Google Drive."
        )

    st.divider()
    st.caption("Sensor Analysis Studio")


# ─────────────────────────────────────────────────────────────────────────────
# Title & tabs
# ─────────────────────────────────────────────────────────────────────────────
st.title("Sensor Analysis Studio")

# ─────────────────────────────────────────────────────────────────────────────
# Mode dispatch
# ─────────────────────────────────────────────────────────────────────────────
if SS.mode == "Cyclic Voltammetry":
    modes.cyclic_voltammetry.render()
elif SS.mode == "Assay":
    modes.assay.render()
elif SS.mode == "Solid-State":
    modes.solid_state.render()
else:
    modes.amperometry.render()
