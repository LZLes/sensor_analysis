"""Pure JSON/CSV helpers for the session bundle, shared by core/persistence.py
(Streamlit) and web_app/api/session.py."""

import io

import numpy as np
import pandas as pd


def _jsonify(obj):
    """Recursively convert numpy scalar types to native Python. Without
    this, a numpy.float64 buried in a dict (e.g. assay_std_res's 4PL fit
    params, which come straight out of scipy.optimize.curve_fit) survives
    into json.dumps(..., default=str) as a STRING instead of a number —
    default=str stringifies anything it can't serialize natively — silently
    corrupting the round-trip. Leaves plain dict/list/str/float/int/bool/
    None untouched."""
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _plate_df_to_csv(plate_df: pd.DataFrame | None) -> str | None:
    return plate_df.to_csv() if plate_df is not None else None


def _plate_df_from_csv(csv_text: str | None) -> pd.DataFrame | None:
    if not csv_text:
        return None
    _df = pd.read_csv(io.StringIO(csv_text), index_col=0)
    _df.columns = pd.Index([int(c) for c in _df.columns], name="Col")
    _df.index.name = "Row"
    return _df
