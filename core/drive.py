"""Google Drive "Cloud Sessions" integration for the sidebar — save/load/
list/delete session JSON files in a shared Drive folder."""

import io
import json as _json

import streamlit as st

try:
    from google.oauth2 import service_account as _gsa
    from googleapiclient.discovery import build as _gbuild
    from googleapiclient.http import MediaIoBaseUpload as _GMediaUpload, MediaIoBaseDownload as _GMediaDownload
    _GDRIVE_LIBS_OK = True
except BaseException:
    # Deliberately catches BaseException, not just Exception: a broken or
    # incompatible crypto backend in the deploy environment can blow up this
    # import with a Rust-side pyo3 PanicException (observed from
    # google-auth's crypto deps), which does not subclass Exception and
    # would otherwise crash the whole app at import time. Either way, Cloud
    # Sessions should just stay unavailable.
    _GDRIVE_LIBS_OK = False


_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _drive_enabled() -> bool:
    return (_GDRIVE_LIBS_OK
            and "gcp_service_account" in st.secrets
            and "gdrive_folder_id" in st.secrets)

@st.cache_resource(show_spinner=False)
def _drive_service():
    info = dict(st.secrets["gcp_service_account"])
    creds = _gsa.Credentials.from_service_account_info(info, scopes=_DRIVE_SCOPES)
    return _gbuild("drive", "v3", credentials=creds, cache_discovery=False)

def _drive_folder_id() -> str:
    return st.secrets["gdrive_folder_id"]

def _drive_esc(name: str) -> str:
    """Escape a name for safe inclusion in a Drive API query string."""
    return name.replace("\\", "\\\\").replace("'", "\\'")

def _drive_list_sessions() -> list[dict]:
    svc = _drive_service()
    q = (f"'{_drive_folder_id()}' in parents and trashed = false "
         "and mimeType = 'application/json'")
    res = svc.files().list(
        q=q, fields="files(id,name,modifiedTime)",
        orderBy="modifiedTime desc", pageSize=200,
    ).execute()
    return res.get("files", [])

def _drive_save_session(name: str, data: bytes) -> None:
    svc = _drive_service()
    fname = f"{name}.json"
    q = (f"'{_drive_folder_id()}' in parents and trashed = false "
         f"and name = '{_drive_esc(fname)}'")
    existing = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    media = _GMediaUpload(io.BytesIO(data), mimetype="application/json", resumable=False)
    if existing:
        svc.files().update(fileId=existing[0]["id"], media_body=media).execute()
    else:
        meta = {"name": fname, "parents": [_drive_folder_id()]}
        svc.files().create(body=meta, media_body=media, fields="id").execute()

def _drive_load_session(file_id: str) -> dict:
    svc = _drive_service()
    buf = io.BytesIO()
    downloader = _GMediaDownload(buf, svc.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return _json.loads(buf.read().decode())

def _drive_delete_session(file_id: str) -> None:
    _drive_service().files().delete(fileId=file_id).execute()
