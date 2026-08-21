"""File parsing: raw CSV / multi-channel-instrument / .pssession import."""

import io

import numpy as np
import pandas as pd
import streamlit as st

from core.numeric import _is_float


def parse_potentiostat_csv(raw: str, sep: str, mode: str = "amperometry") -> tuple[pd.DataFrame, list[dict]]:
    """
    Parse multi-channel potentiostat exports (Bio-Logic, CH Instruments, etc.).

    Format assumed:
        • N metadata rows (Date, Notes, blank, …)
        • One channel-label row: 'CH1: …', '', 'CH2: …', '', …
        • Zero or more non-numeric rows (measurement date, etc.)
        • One units row: 's', 'µA', 's', 'µA', …  (or 'V', 'µA' for CV)
        • Numeric data rows

    mode='amperometry': returns channels with {name, tc, ic} (time + current)
    mode='cv':          returns channels with {name, vc, ic} (voltage + current)
    """
    import re as _re
    engine = "python" if sep == r"\s+" else "c"
    _split = _re.compile(sep).split if sep == r"\s+" else lambda ln: ln.split(sep)
    max_cols = max(
        (len(_split(ln.strip())) for ln in raw.splitlines() if ln.strip()),
        default=1,
    )
    all_df = pd.read_csv(
        io.StringIO(raw), sep=sep, header=None, dtype=str,
        engine=engine, skipinitialspace=True,
        names=range(max_cols),
    )
    all_df.columns = list(range(all_df.shape[1]))

    def row_numeric(row) -> bool:
        vals = [v for v in row if pd.notna(v) and str(v).strip() not in ("", "nan")]
        return bool(vals) and all(_is_float(v) for v in vals)

    data_start = next(
        (i for i, (_, r) in enumerate(all_df.iterrows()) if row_numeric(r)),
        None,
    )
    if data_start is None:
        raise ValueError("No numeric data rows found — check delimiter.")

    units_row = all_df.iloc[data_start - 1] if data_start >= 1 else None

    # Find channel-label row: highest row before data containing 'CH'
    ch_row = None
    for i in range(data_start - 2, -1, -1):
        cells = [str(v).strip() for v in all_df.iloc[i]
                 if pd.notna(v) and str(v).strip() not in ("", "nan")]
        if any("CH" in c or "channel" in c.lower() for c in cells):
            ch_row = all_df.iloc[i]
            break

    # Build column names: "CH1 (s)", "CH1 (µA)", "CH2 (s)", …
    n_cols = all_df.shape[1]
    col_names: list[str] = []
    last_ch = "CH"
    for c in range(n_cols):
        if ch_row is not None:
            cell = str(ch_row.iloc[c]).strip()
            if cell and cell not in ("nan", ""):
                last_ch = cell.split(":")[0].strip()
        unit = (str(units_row.iloc[c]).strip()
                if units_row is not None else str(c))
        if unit in ("nan", ""):
            unit = str(c)
        col_names.append(f"{last_ch} ({unit})")

    data = all_df.iloc[data_start:].copy()
    data.columns = col_names
    data = data.apply(
        lambda s: pd.to_numeric(s.str.replace(",", "."), errors="coerce")
    )
    df = data.reset_index(drop=True)

    # Auto-infer channel pairs from column names
    _cv_mode = (mode == "cv")
    _x_units = (
        {"v", "mv", "volt", "volts", "potential", "e/v", "e / v"}
        if _cv_mode else
        {"s", "sec", "seconds", "ms", "min"}
    )
    _x_key = "vc" if _cv_mode else "tc"
    _current_units = {"µa", "ua", "na", "ma", "a", "µA", "nA", "mA"}
    from collections import defaultdict
    groups: dict[str, dict] = defaultdict(dict)
    for col in df.columns:
        if " (" in col and col.endswith(")"):
            prefix = col[: col.rfind(" (")]
            unit   = col[col.rfind(" (") + 2 : -1]
            if unit.lower() in _x_units:
                groups[prefix][_x_key] = col
            elif unit.lower() in _current_units or unit in _current_units:
                groups[prefix]["ic"] = col
    channels = [
        {"name": name, _x_key: m[_x_key], "ic": m["ic"]}
        for name, m in groups.items()
        if _x_key in m and "ic" in m
    ]

    return df, channels


def _ps_unit(raw: str | None, fallback: str) -> str:
    """Extract bare unit from strings like 'Time / s' → 's'."""
    if not raw:
        return fallback
    s = raw.strip()
    return s.split("/")[-1].strip() if "/" in s else s


def parse_pssession(file_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    """
    Parse a PalmSens .pssession file (ZIP archive containing XML).
    Returns (df, channels) compatible with the rest of the app.

    Tries three common XML layouts used across PSTrace versions:
      1. <Curve> with <Point X="…" Y="…"/> children
      2. <DataSet> with <Time> and <I> text lists
      3. <Values> with <Value>t,i</Value> pairs
    """
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        with zf:
            xml_files = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            target = xml_files[0] if xml_files else (zf.namelist() or [None])[0]
            if target is None:
                raise ValueError("Empty .pssession archive.")
            with zf.open(target) as f:
                root = ET.parse(f).getroot()
    except zipfile.BadZipFile:
        # Older PSTrace versions save plain XML directly
        try:
            root = ET.fromstring(file_bytes)
        except ET.ParseError as e:
            raise ValueError(
                f".pssession is neither a ZIP archive nor valid XML: {e}"
            )

    # (name, x_unit, y_unit, times, currents)
    records: list[tuple[str, str, str, list, list]] = []

    # Strategy 1: <Curve> elements with <Point X="…" Y="…"/>
    for i, curve in enumerate(root.findall(".//Curve")):
        x_unit = _ps_unit(curve.findtext("XUnit") or curve.findtext("XTitle"), "s")
        y_unit = _ps_unit(curve.findtext("YUnit") or curve.findtext("YTitle"), "µA")
        points = curve.findall(".//Point")
        if points and "X" in points[0].attrib:
            try:
                times    = [float(p.get("X", "nan")) for p in points]
                currents = [float(p.get("Y", "nan")) for p in points]
                name = curve.get("Title") or curve.get("Name") or f"CH{i + 1}"
                records.append((name, x_unit, y_unit, times, currents))
            except ValueError:
                pass

    # Strategy 2: <DataSet> with separate <Time>/<I> whitespace-delimited text
    if not records:
        for i, ds in enumerate(root.findall(".//DataSet")):
            t_el = ds.find("Time") or ds.find("T")
            i_el = ds.find("I") or ds.find("Current")
            if t_el is not None and i_el is not None and t_el.text and i_el.text:
                try:
                    times    = [float(v) for v in t_el.text.split()]
                    currents = [float(v) for v in i_el.text.split()]
                    records.append((f"CH{i + 1}", "s", "µA", times, currents))
                except ValueError:
                    pass

    # Strategy 3: <Values> → <Value>t,i</Value> comma-separated pairs
    if not records:
        for i, vel in enumerate(root.findall(".//Values")):
            rows_xy = []
            for v in vel.findall("Value"):
                txt = (v.text or "").strip()
                if "," in txt:
                    try:
                        a, b = txt.split(",", 1)
                        rows_xy.append((float(a), float(b)))
                    except ValueError:
                        pass
            if rows_xy:
                times, currents = zip(*rows_xy)
                records.append((f"CH{i + 1}", "s", "µA", list(times), list(currents)))

    if not records:
        children = ", ".join(f"<{c.tag}>" for c in list(root)[:8])
        raise ValueError(
            f"Could not extract time/current data from this .pssession file. "
            f"Root element: <{root.tag}>, first children: {children}. "
            f"Share this info so the parser can be extended for your format version."
        )

    max_len = max(len(r[3]) for r in records)
    col_data: dict[str, np.ndarray] = {}
    channels: list[dict] = []
    for name, x_unit, y_unit, times, currents in records:
        t_col = f"{name} ({x_unit})"
        i_col = f"{name} ({y_unit})"
        t_arr = np.full(max_len, np.nan)
        t_arr[: len(times)] = times
        i_arr = np.full(max_len, np.nan)
        i_arr[: len(currents)] = currents
        col_data[t_col] = t_arr
        col_data[i_col] = i_arr
        channels.append({"name": name, "tc": t_col, "ic": i_col})

    return pd.DataFrame(col_data), channels


def _parse_one_file(_up, _fi: int, key_prefix: str = "amp") -> tuple[pd.DataFrame | None, list[dict]]:
    """Parse one uploaded file, returning (df, auto_channels). key_prefix
    keeps this file's format/delimiter/skip widgets independent when the
    same import UI is reused across modes (Amperometry vs Solid-State)."""
    if _up.name.lower().endswith(".pssession"):
        _df, _auto = parse_pssession(_up.read())
        return _df, _auto

    _raw_bytes = _up.read()
    if _raw_bytes[:2] in (b"\xff\xfe", b"\xfe\xff"):
        _raw = _raw_bytes.decode("utf-16")
    else:
        _raw = _raw_bytes.decode("utf-8", errors="replace")

    _file_fmt = st.selectbox(
        "File format",
        ["Standard CSV", "Multi-channel instrument (potentiostat, etc.)"],
        help=(
            "Choose **Multi-channel instrument** for files exported from Bio-Logic, "
            "CH Instruments, Autolab, or similar — they have metadata rows, "
            "channel-label rows, and a units row above the numeric data."
        ),
        key=f"{key_prefix}_file_fmt_{_fi}",
    )

    _c1, _c2 = st.columns(2)
    _delim_label = _c1.selectbox(
        "Delimiter",
        ["Auto-detect", "Comma  ,", "Tab  \\t", "Semicolon  ;", "Space"],
        help="Choose the character that separates columns. Auto-detect works for most files.",
        key=f"{key_prefix}_delim_{_fi}",
    )
    _skip = int(_c2.number_input(
        "Rows to skip before header", 0, 50, 0,
        help="Only applies to Standard CSV mode. Multi-channel mode finds the data start automatically.",
        key=f"{key_prefix}_skip_{_fi}",
    ))

    _dmap = {
        "Auto-detect": None,
        "Comma  ,": ",", "Tab  \\t": "\t",
        "Semicolon  ;": ";", "Space": r"\s+",
    }
    _d = _dmap[_delim_label]
    if _d is None:
        _lines = _raw.splitlines()
        _sniff_line = _lines[_skip] if _skip < len(_lines) else (_lines[0] if _lines else "")
        _d = next((c for c in [",", "\t", ";"] if c in _sniff_line), r"\s+")

    _engine = "python" if _d == r"\s+" else "c"

    if _file_fmt.startswith("Multi-channel"):
        _df, _auto = parse_potentiostat_csv(_raw, _d)
        return _df, _auto
    _df = pd.read_csv(
        io.StringIO(_raw), sep=_d, skiprows=_skip,
        engine=_engine, skipinitialspace=True,
    )
    _df.columns = [c.lstrip("﻿").strip() for c in _df.columns]
    return _df, []
