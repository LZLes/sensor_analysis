"""Shared helpers for web_app/api/*.py — JSON-safe DataFrame<->records
conversion and figure/file response builders. No mode-specific logic here;
each mode's router owns its own endpoints and fit-calling code."""

from __future__ import annotations

import json

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


def records_to_df(records: list[dict], columns: list[str]) -> pd.DataFrame:
    """List of row dicts (from a request body) -> DataFrame with a fixed
    column order, filling any column missing from a record with NaN."""
    if not records:
        return pd.DataFrame({c: [] for c in columns})
    df = pd.DataFrame(records)
    for c in columns:
        if c not in df.columns:
            df[c] = np.nan
    return df[columns]


def figure_json(fig: go.Figure) -> dict:
    """go.Figure -> JSON-safe dict via Plotly's own numpy-safe encoder
    (fig.to_json()), not FastAPI's default one — see plot_view.py's
    equivalent reasoning for why this matters with numpy-heavy traces."""
    return json.loads(fig.to_json())


def png_response(content: bytes, filename: str, media_type: str = "image/png") -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def require_file_index(files: list[dict], index: int) -> dict:
    if not (0 <= index < len(files)):
        raise HTTPException(status_code=404, detail=f"No file at index {index}")
    return files[index]
