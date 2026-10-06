"""Shared helpers for web_app/api/*.py — JSON-safe DataFrame<->records
conversion and figure/file response builders. No mode-specific logic here;
each mode's router owns its own endpoints and fit-calling code."""

from __future__ import annotations

import json
import re
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from fastapi import HTTPException
from fastapi.responses import Response


def _clean_scalar(v):
    """NaN/NaT -> None (JSON has no NaN token JS can parse), numpy scalar
    -> native Python (same problem core/persistence.py's _jsonify docstring
    describes for embedded numpy types surviving into json.dumps)."""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.generic):
        return v.item()
    return v


def df_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe list of row dicts."""
    return [{k: _clean_scalar(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def records_to_df(records: list[dict], columns: list[str],
                  text_cols: tuple[str, ...] = ("Label",),
                  bool_cols: tuple[str, ...] = ()) -> pd.DataFrame:
    """List of row dicts (from a request body) -> DataFrame with a fixed
    column order, filling any column missing from a record with NaN.

    Every column not listed in text_cols/bool_cols is coerced to numeric
    (unparseable text -> NaN), so a typo typed into a table cell can't reach
    the fit code as a string and crash it with a bare 500."""
    if not records:
        return pd.DataFrame({c: [] for c in columns})
    df = pd.DataFrame(records)
    for c in columns:
        if c not in df.columns:
            df[c] = np.nan
    df = df[columns].copy()
    for c in columns:
        if c in text_cols:
            df[c] = df[c].apply(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
        elif c in bool_cols:
            df[c] = df[c].apply(lambda v: bool(v) if pd.notna(v) else False)
        else:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def figure_json(fig: go.Figure) -> dict:
    """go.Figure -> JSON-safe dict via Plotly's own numpy-safe encoder
    (fig.to_json()), not FastAPI's default one, which chokes on numpy
    int64/float64 values inside traces."""
    return json.loads(fig.to_json())


_MEDIA_TYPES = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf", "tiff": "image/tiff"}
ExportFmt = Literal["png", "svg", "pdf", "tiff"]
ExportStyle = Literal["default", "origin", "minimal"]


def export_media_type(fmt: str) -> str:
    if fmt not in _MEDIA_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported export format {fmt!r}")
    return _MEDIA_TYPES[fmt]


def png_response(content: bytes, filename: str, media_type: str = "image/png") -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def channels_fit_columns(channels: list[dict], columns, keys: tuple[str, ...] = ("tc", "ic")) -> bool:
    """True if every channel's referenced columns exist in the DataFrame."""
    cols = set(columns)
    return all(ch.get(k) in cols for ch in channels for k in keys)


def guess_channels(df: pd.DataFrame) -> list[dict]:
    """Best-guess channel mapping for a plain CSV with no instrument channel
    metadata: the time column is the first one whose header mentions time
    (else the first column), and every other mostly-numeric column becomes
    its own channel, named after its header minus any "(unit)" suffix."""
    columns = list(df.columns)
    if not columns:
        return []
    time_col = next((c for c in columns if re.search(r"\btime\b|^t\s*[\(\[]|^t$", str(c), re.I)), columns[0])
    channels = []
    for c in columns:
        if c == time_col:
            continue
        numeric = pd.to_numeric(df[c], errors="coerce")
        if numeric.notna().mean() < 0.5:
            continue
        name = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", str(c)).strip() or str(c)
        channels.append({"name": name, "tc": time_col, "ic": c})
    names = [ch["name"] for ch in channels]
    if len(set(names)) != len(names):  # fall back to raw headers if stripping units collided
        for ch in channels:
            ch["name"] = str(ch["ic"])
    return channels or [{"name": "Channel 1", "tc": columns[0], "ic": columns[min(1, len(columns) - 1)]}]


def require_file_index(files: list[dict], index: int) -> dict:
    if not (0 <= index < len(files)):
        raise HTTPException(status_code=404, detail=f"No file at index {index}")
    return files[index]
