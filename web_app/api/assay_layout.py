"""
Pure plate-layout, grouping and normalisation helpers for the web app's Assay
mode (no FastAPI/session code here, so they're unit-testable on their own).

Layout model, shared with the Streamlit app's session keys:
  * std_df (Label, Conc, S1, S2, S3): one row per concentration level, up to
    3 replicate wells. Row 0 is the blank (its mean is subtracted from every
    well), exactly as in modes/assay.py.
  * sample_df (Well, Label, Subject, Timepoint): one row per sample well.
    Streamlit only reads Well/Label; Subject/Timepoint ride along as extra
    columns. Label is composed from them ("P01 · D7") when left blank, so a
    session opened in Streamlit still shows meaningful sample names.

The assign_* functions return NEW DataFrames (inputs untouched) plus a
human-readable summary, and raise ValueError with a user-facing message.
"""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

ROWS = "ABCDEFGH"
STD_COLUMNS = ["Label", "Conc", "S1", "S2", "S3"]
SAMPLE_COLUMNS = ["Well", "Label", "Subject", "Timepoint"]
NORM_COLUMNS = ["Subject", "Timepoint", "Dilution", "Volume", "Area"]
_WELL_RE = re.compile(r"^([A-H])(1[0-2]|0?[1-9])$")


# -- small utilities -----------------------------------------------------------------
def txt(v) -> str:
    """Cell value -> clean string ("" for None/NaN)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def well_rc(w: str) -> tuple[int, int] | None:
    m = _WELL_RE.match(txt(w).upper())
    return (ROWS.index(m.group(1)), int(m.group(2)) - 1) if m else None


def well_name(r: int, c: int) -> str:
    return f"{ROWS[r]}{c + 1}"


def clean_wells(wells) -> list[str]:
    """Validate + dedupe (keeping order); 'a01' -> 'A1'."""
    out, bad = [], []
    for w in wells:
        rc = well_rc(w)
        if rc is None:
            bad.append(txt(w))
        else:
            name = well_name(*rc)
            if name not in out:
                out.append(name)
    if bad:
        raise ValueError(f"Not a valid well (A1–H12): {', '.join(bad)}")
    if not out:
        raise ValueError("Select at least one well on the plate first.")
    return out


def sort_wells(wells: list[str], order: str = "rows") -> list[str]:
    """'rows' = A1, A2 … A12, B1 …; 'columns' = A1, B1 … H1, A2 …"""
    def key(w):
        r, c = well_rc(w)
        return (r, c) if order == "rows" else (c, r)
    return sorted(wells, key=key)


def compose_label(subject: str, timepoint: str) -> str:
    return " · ".join(p for p in (subject, timepoint) if p)


def fmt_conc(x: float) -> str:
    return f"{x:.6g}"


def normalize_sample_df(df: pd.DataFrame | None) -> pd.DataFrame:
    """Any sample table (incl. old Streamlit ones with only Well/Label) -> the
    four text columns, with Label filled in from Subject/Timepoint."""
    if df is None or len(df) == 0:
        return pd.DataFrame({c: pd.Series([], dtype=str) for c in SAMPLE_COLUMNS})
    out = df.reindex(columns=SAMPLE_COLUMNS).copy()
    for c in SAMPLE_COLUMNS:
        out[c] = out[c].map(txt)
    out["Well"] = out["Well"].str.upper()
    blank_label = out["Label"] == ""
    out.loc[blank_label, "Label"] = [compose_label(s, t) for s, t in
                                     zip(out.loc[blank_label, "Subject"], out.loc[blank_label, "Timepoint"])]
    return out[out["Well"] != ""].reset_index(drop=True)


def _without_wells_std(std_df: pd.DataFrame, wells: set[str]) -> pd.DataFrame:
    """Blank out the given wells in the standards table; drop non-blank
    levels left with no wells at all."""
    std = std_df.reindex(columns=STD_COLUMNS).copy()
    for c in ("S1", "S2", "S3"):
        std[c] = std[c].map(lambda v: "" if txt(v).upper() in wells else txt(v).upper())
    keep = [i == 0 or any(std.iloc[i][c] for c in ("S1", "S2", "S3")) for i in range(len(std))]
    return std[keep].reset_index(drop=True)


def _without_wells_samples(sample_df: pd.DataFrame, wells: set[str]) -> pd.DataFrame:
    s = normalize_sample_df(sample_df)
    return s[~s["Well"].isin(wells)].reset_index(drop=True)


# -- name lists: "P01-P12", "D0, D3, D7", "0-24 by 6" ---------------------------------------
_RANGE_RE = re.compile(
    r"^(?P<pre>.*?)(?P<a>\d+)(?P<suf>[^\d\s\-–]*)\s*(?:-|–|\.\.|\bto\b)\s*"
    r"(?P<pre2>\D*?)(?P<b>\d+)(?P<suf2>[^\d\s]*)(?:\s*(?:by|step)\s*(?P<step>\d+))?$",
    re.IGNORECASE,
)


def parse_name_list(text: str) -> list[str]:
    """Split on commas/semicolons/newlines/tabs and expand numeric ranges:
    'P01-P12' -> P01 … P12 (zero padding kept), 'Day 0 - Day 14 by 7' ->
    Day 0, Day 7, Day 14. Anything that doesn't look like a sane range
    (e.g. 'WT-1', '2024-01') is kept literally."""
    out: list[str] = []
    for tok in re.split(r"[,;\n\t]+", text or ""):
        tok = tok.strip()
        if not tok:
            continue
        m = _RANGE_RE.match(tok)
        if m:
            pre, pre2 = m.group("pre"), m.group("pre2")
            suf, suf2 = m.group("suf"), m.group("suf2")
            a, b = int(m.group("a")), int(m.group("b"))
            step = int(m.group("step") or 1)
            same_affix = pre2.strip() in ("", pre.strip()) and suf2 in ("", suf) and (not suf or suf2 in ("", suf))
            if same_affix and step > 0 and a < b and (b - a) // step < 500:
                width = len(m.group("a")) if m.group("a").startswith("0") and len(m.group("a")) > 1 else 0
                out.extend(f"{pre}{str(n).zfill(width)}{suf or suf2}" for n in range(a, b + 1, step))
                continue
        out.append(tok)
    return out


# -- standards ------------------------------------------------------------------------
def serial_concs(n: int, top: float, factor: float, include_blank: bool, lowest_first: bool) -> list[float]:
    if n < 1:
        return []
    if not (np.isfinite(top) and top > 0):
        raise ValueError("Top concentration must be a positive number.")
    if not (np.isfinite(factor) and factor > 1):
        raise ValueError("Dilution factor must be greater than 1 (e.g. 2 for a 1:2 series).")
    n_dil = n - 1 if include_blank else n
    concs = [float(f"{top / factor ** i:.6g}") for i in range(n_dil)]
    if include_blank:
        concs.append(0.0)
    return concs[::-1] if lowest_first else concs


def parse_conc_list(text: str) -> list[float]:
    vals = []
    for tok in re.split(r"[,;\s]+", (text or "").strip()):
        if not tok:
            continue
        try:
            vals.append(float(tok.replace("−", "-")))
        except ValueError as exc:
            raise ValueError(f"'{tok}' in the concentration list isn't a number.") from exc
    return vals


def group_levels(wells: list[str], direction: str) -> list[list[str]]:
    """Split a selection into concentration levels.
    direction='across': each plate COLUMN is a level, its rows are replicates
    (the default A1:C8 layout). direction='down': each ROW is a level."""
    by: dict[int, list[str]] = {}
    for w in wells:
        r, c = well_rc(w)
        by.setdefault(c if direction == "across" else r, []).append(w)
    order = "columns" if direction == "across" else "rows"
    return [sort_wells(by[k], order) for k in sorted(by)]


def assign_standards(std_df: pd.DataFrame, sample_df: pd.DataFrame, wells, direction: str,
                     concs: list[float]) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Replace the standards series with the selected block. A level with
    concentration 0 becomes the blank (row 0); otherwise an existing blank
    elsewhere on the plate is kept."""
    wells = clean_wells(wells)
    levels = group_levels(wells, direction)
    too_many = [i + 1 for i, lv in enumerate(levels) if len(lv) > 3]
    if too_many:
        along = "rows" if direction == "across" else "columns"
        raise ValueError(f"Each level can have at most 3 replicate wells (Set 1–3), but the selection spans "
                         f"{max(len(lv) for lv in levels)} {along}. Select at most 3 {along}, or switch the "
                         "replicate direction.")
    if len(concs) != len(levels):
        raise ValueError(f"The selection has {len(levels)} concentration level(s) but {len(concs)} "
                         f"concentration(s) were given.")
    if sum(1 for c in concs if c == 0) > 1:
        raise ValueError("Only one level can have concentration 0 (the blank).")

    pairs = list(zip(concs, levels))
    blank = [p for p in pairs if p[0] == 0]
    stds = sorted([p for p in pairs if p[0] != 0], key=lambda p: p[0])
    rows = []
    sel = set(wells)
    note = ""
    if blank:
        rows.append({"Label": "Blank", "Conc": 0.0, **_sets(blank[0][1])})
    else:
        old = std_df.reindex(columns=STD_COLUMNS)
        old_blank_wells = [txt(old.iloc[0][c]).upper() for c in ("S1", "S2", "S3")] if len(old) else []
        old_conc = pd.to_numeric(old.iloc[0]["Conc"], errors="coerce") if len(old) else np.nan
        if old_conc == 0 and any(old_blank_wells) and not (set(old_blank_wells) & sel):
            rows.append({"Label": "Blank", "Conc": 0.0, **{f"S{i + 1}": w for i, w in enumerate(old_blank_wells)}})
            note = f" Kept the existing blank ({', '.join(w for w in old_blank_wells if w)})."
        else:
            note = " No blank yet — select the blank wells and use Mark as blank."
    rows += [{"Label": f"Std {fmt_conc(c)}", "Conc": float(c), **_sets(lv)} for c, lv in stds]
    std = pd.DataFrame(rows, columns=STD_COLUMNS)
    msg = (f"{len(levels)} level(s) × up to {max(len(lv) for lv in levels)} replicate(s) assigned as standards."
           + note)
    return std, _without_wells_samples(sample_df, sel), msg


def _sets(level_wells: list[str]) -> dict:
    return {f"S{i + 1}": (level_wells[i] if i < len(level_wells) else "") for i in range(3)}


def assign_blank(std_df: pd.DataFrame, sample_df: pd.DataFrame, wells) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    wells = clean_wells(wells)
    if len(wells) > 3:
        raise ValueError(f"The blank can have at most 3 replicate wells; {len(wells)} are selected.")
    sel = set(wells)
    std = _without_wells_std(std_df, sel)
    blank_row = {"Label": "Blank", "Conc": 0.0, **_sets(sort_wells(wells))}
    has_blank = len(std) and pd.to_numeric(std.iloc[0]["Conc"], errors="coerce") == 0
    body = std.iloc[1:] if has_blank else std
    std = pd.concat([pd.DataFrame([blank_row], columns=STD_COLUMNS), body], ignore_index=True)
    # A level emptied by moving its wells to the blank isn't useful any more.
    keep = [i == 0 or any(txt(std.iloc[i][c]) for c in ("S1", "S2", "S3")) for i in range(len(std))]
    std = std[keep].reset_index(drop=True)
    return std, _without_wells_samples(sample_df, sel), f"Blank set to {', '.join(sort_wells(wells))}."


# -- samples --------------------------------------------------------------------------
def plan_samples(wells, subjects: list[str], timepoints: list[str], replicates: int, order: str,
                 nesting: str) -> tuple[list[dict], str]:
    """Walk the selected wells in `order` and give each run of `replicates`
    wells the next (subject, timepoint) combination. nesting='subject' keeps
    each subject's timepoints together (P1 D0, P1 D3, …, P2 D0 …);
    'timepoint' keeps each timepoint's subjects together."""
    wells = sort_wells(clean_wells(wells), order)
    if not subjects and not timepoints:
        raise ValueError("Enter at least one subject (or timepoint).")
    if replicates < 1:
        raise ValueError("Replicates must be at least 1.")
    subjects = subjects or [""]
    timepoints = timepoints or [""]
    if nesting == "timepoint":
        combos = [(s, t) for t in timepoints for s in subjects]
    else:
        combos = [(s, t) for s in subjects for t in timepoints]
    plan = []
    for i, w in enumerate(wells):
        k = i // replicates
        if k >= len(combos):
            break
        s, t = combos[k]
        plan.append({"Well": w, "Subject": s, "Timepoint": t, "Label": compose_label(s, t), "Rep": i % replicates + 1})
    n_full = len(wells) // replicates
    need = len(combos) * replicates
    msg = f"{len(combos)} sample(s) × {replicates} replicate(s) = {need} wells; {len(wells)} selected."
    if len(wells) < need:
        missing = len(combos) - n_full
        msg += f" Not enough wells: the last {missing} sample(s) won't be placed."
    elif len(wells) > need:
        msg += f" {len(wells) - need} extra well(s) will be left unlabelled."
    elif len(wells) % replicates:
        msg += " The last sample has fewer replicates."
    return plan, msg


def assign_samples(std_df: pd.DataFrame, sample_df: pd.DataFrame, plan: list[dict],
                   selected) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply a plan from plan_samples: the selected wells are taken out of
    the standards and relabelled (extra selected wells lose their label)."""
    sel = set(clean_wells(selected))
    std = _without_wells_std(std_df, sel)
    samples = _without_wells_samples(sample_df, sel)
    new = pd.DataFrame([{c: p[c] for c in SAMPLE_COLUMNS} for p in plan], columns=SAMPLE_COLUMNS)
    samples = pd.concat([samples, new], ignore_index=True)
    return std, normalize_sample_df(samples)


def clear_wells(std_df: pd.DataFrame, sample_df: pd.DataFrame, wells) -> tuple[pd.DataFrame, pd.DataFrame]:
    sel = set(clean_wells(wells))
    return _without_wells_std(std_df, sel), _without_wells_samples(sample_df, sel)


# -- pasted layout grid ---------------------------------------------------------------
_BLANK_RE = re.compile(r"^(blank|blk|bkg|background|zero)$", re.IGNORECASE)
_STD_RE = re.compile(r"^(?:std|standard|cal|calibrator)\s*[:#=_-]?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*\S*$",
                     re.IGNORECASE)
_SKIP_RE = re.compile(r"^(-+|x|empty|n/?a|none)$", re.IGNORECASE)


def _split_cells(line: str) -> list[str]:
    if "\t" in line:
        return line.split("\t")
    if ";" in line:
        return line.split(";")
    return line.split(",")


def parse_layout_grid(text: str) -> list[tuple[str, str]]:
    """An 8×12 block of well labels (as copied from Excel) -> [(well, cell)].
    Optional A–H row letters and a 1–12 header row are recognised."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Paste the layout block first.")
    grid = [_split_cells(ln.rstrip("\r")) for ln in lines]
    first = [c.strip() for c in grid[0]]
    nums = [c for c in first if c]
    if nums and all(c.isdigit() for c in nums) and nums[:3] == ["1", "2", "3"]:
        grid = grid[1:]
    out = []
    has_letters = sum(1 for row in grid if row and re.fullmatch(r"[A-Ha-h]", row[0].strip())) >= min(len(grid), 2)
    for i, row in enumerate(grid):
        if has_letters:
            if not row or not re.fullmatch(r"[A-Ha-h]", row[0].strip()):
                continue
            r = ROWS.index(row[0].strip().upper())
            cells = row[1:]
        else:
            if i >= 8:
                break
            r, cells = i, row
        for c, cell in enumerate(cells[:12]):
            cell = cell.strip().strip('"')
            if cell and not _SKIP_RE.match(cell):
                out.append((well_name(r, c), cell))
    if not out:
        raise ValueError("No labels found in the pasted block.")
    return out


def split_label(cell: str, sep: str) -> tuple[str, str]:
    """'P01_D7' -> ('P01', 'D7'). sep='auto' tries _, /, | then whitespace."""
    if sep == "none":
        return cell, ""
    seps = ["_", "/", "|", " "] if sep == "auto" else [sep]
    for s in seps:
        if s == " ":
            parts = cell.split(None, 1)
        else:
            parts = cell.split(s, 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), parts[1].strip()
    return cell, ""


def apply_layout_grid(std_df: pd.DataFrame, cells: list[tuple[str, str]], sep: str
                      ) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Build a whole layout from pasted cells. 'Blank' cells -> the blank,
    'Std 10' cells -> a standard at 10, anything else -> a sample split into
    subject/timepoint. Standards in the paste replace the standards table;
    without any, the current standards are kept (minus overlapping wells)."""
    std_cells: dict[float, list[str]] = {}
    samples = []
    for well, cell in cells:
        if _BLANK_RE.match(cell):
            std_cells.setdefault(0.0, []).append(well)
            continue
        m = _STD_RE.match(cell)
        if m:
            std_cells.setdefault(float(m.group(1)), []).append(well)
            continue
        s, t = split_label(cell, sep)
        samples.append({"Well": well, "Subject": s, "Timepoint": t, "Label": compose_label(s, t)})
    for conc, ws in std_cells.items():
        if len(ws) > 3:
            raise ValueError(f"Standard {fmt_conc(conc)} appears in {len(ws)} wells; at most 3 replicates are supported.")
    sample_wells = {p["Well"] for p in samples}
    if std_cells:
        rows = []
        if 0.0 in std_cells:
            rows.append({"Label": "Blank", "Conc": 0.0, **_sets(sort_wells(std_cells.pop(0.0)))})
        rows += [{"Label": f"Std {fmt_conc(c)}", "Conc": c, **_sets(sort_wells(ws))} for c, ws in sorted(std_cells.items())]
        std = pd.DataFrame(rows, columns=STD_COLUMNS)
        std_msg = f"{len(rows)} standard level(s)"
    else:
        std = _without_wells_std(std_df, sample_wells)
        std_msg = "standards unchanged"
    sample_df = normalize_sample_df(pd.DataFrame(samples, columns=SAMPLE_COLUMNS))
    n_subj = len({p["Subject"] for p in samples})
    n_tp = len({p["Timepoint"] for p in samples} - {""})
    msg = f"Layout pasted: {std_msg}, {len(samples)} sample well(s) ({n_subj} subject(s)"
    msg += f", {n_tp} timepoint(s))." if n_tp else ")."
    return std, sample_df, msg


# -- grouping + normalisation ------------------------------------------------------------
def group_key(row: dict) -> tuple[str, str]:
    """(Subject, Timepoint); a sample with only a free-text Label groups by
    that label; an unlabelled well stays on its own."""
    s, t = txt(row.get("Subject")), txt(row.get("Timepoint"))
    if s or t:
        return s, t
    return txt(row.get("Label")) or txt(row.get("Well")), ""


def group_results(rows: list[dict]) -> list[dict]:
    """Per-well results -> one row per sample: n, mean/SD/CV of the
    back-calculated concentration (undefined wells excluded)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)
    out = []
    for (s, t), rs in groups.items():
        vals = np.array([r["Conc"] for r in rs if r["Conc"] is not None and np.isfinite(r["Conc"])], dtype=float)
        n = len(vals)
        mean = float(vals.mean()) if n else np.nan
        sd = float(vals.std(ddof=1)) if n > 1 else np.nan
        cv = abs(sd / mean) * 100 if n > 1 and mean else np.nan
        flags = sorted({r["Flag"] for r in rs if r["Flag"]})
        out.append({"Subject": s, "Timepoint": t, "n": n, "Mean": mean, "SD": sd, "CV": cv,
                    "Wells": ", ".join(r["Well"] for r in rs), "Flag": ", ".join(flags)})
    return out


def ordered_unique(values) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen


_MOLAR = {"M": 0, "mM": -3, "µM": -6, "uM": -6, "μM": -6, "nM": -9, "pM": -12, "fM": -15}
_MASS_PER_L = {"g/L": 0, "mg/L": -3, "µg/L": -6, "ug/L": -6, "ng/L": -9, "pg/L": -12,
               "g/mL": 3, "mg/mL": 0, "µg/mL": -3, "ug/mL": -3, "ng/mL": -6, "pg/mL": -9, "fg/mL": -12,
               "mg/dL": -2, "µg/dL": -5, "ng/dL": -8,
               "µg/µL": 0, "ug/uL": 0, "ng/µL": -3, "ng/uL": -3, "pg/µL": -6, "pg/uL": -6}
_VOLUME = {"L": 0, "mL": -3, "µL": -6, "uL": -6, "μL": -6, "nL": -9}
_PREFIX = {3: "k", 0: "", -3: "m", -6: "µ", -9: "n", -12: "p", -15: "f", -18: "a", -21: "z"}


def amount_unit(conc_unit: str, vol_unit: str) -> tuple[float, str]:
    """(factor, unit) so that conc × volume × factor is in `unit`:
    µM × µL -> (1, 'pmol'), ng/mL × µL -> (1, 'pg'). Unknown units fall back
    to (1, 'conc·vol')."""
    cu, vu = conc_unit.strip(), vol_unit.strip()
    if vu in _VOLUME and (cu in _MOLAR or cu in _MASS_PER_L):
        base, e_c = ("mol", _MOLAR[cu]) if cu in _MOLAR else ("g", _MASS_PER_L[cu])
        e = e_c + _VOLUME[vu]
        e3 = max(-21, min(3, 3 * math.floor(e / 3)))
        return 10.0 ** (e - e3), _PREFIX[e3] + base
    return 1.0, f"{cu}·{vu}"


def normalise(groups: list[dict], inputs: list[dict], conc_unit: str, vol_unit: str, area_unit: str
              ) -> tuple[list[dict], dict]:
    """Per sample: sample conc = mean × dilution; amount = sample conc ×
    volume (only when a volume is given); per area = amount (or sample conc
    when there's no volume) ÷ area. SD scales by the same factors."""
    by_key = {(txt(r.get("Subject")), txt(r.get("Timepoint"))): r for r in inputs}
    factor, amt_unit = amount_unit(conc_unit, vol_unit)
    # Once any sample has a volume, per-area is amount/area for everyone (a
    # sample without a volume gets no per-area value rather than a value in
    # different units).
    keys = [(g["Subject"], g["Timepoint"]) for g in groups]
    any_volume = any(np.isfinite(_num(by_key[k].get("Volume"))) for k in keys if k in by_key)
    out = []
    for g in groups:
        inp = by_key.get((g["Subject"], g["Timepoint"]), {})
        dil = _num(inp.get("Dilution"), 1.0)
        vol = _num(inp.get("Volume"))
        area = _num(inp.get("Area"))
        mean, sd = g["Mean"] * dil, g["SD"] * dil
        row = {"Subject": g["Subject"], "Timepoint": g["Timepoint"], "n": g["n"],
               "Dilution": dil, "Volume": vol, "Area": area,
               "SampleConc": mean, "SampleSD": sd, "Amount": np.nan, "AmountSD": np.nan,
               "PerArea": np.nan, "PerAreaSD": np.nan, "Flag": g["Flag"]}
        base, base_sd = (mean, sd) if not any_volume else (np.nan, np.nan)
        if np.isfinite(vol):
            row["Amount"], row["AmountSD"] = mean * vol * factor, sd * vol * factor
            base, base_sd = row["Amount"], row["AmountSD"]
        if np.isfinite(area) and area > 0:
            row["PerArea"], row["PerAreaSD"] = base / area, base_sd / area
        out.append(row)
    num_unit = amt_unit if any_volume else conc_unit
    units = {"sample": conc_unit, "amount": amt_unit, "per_area": f"{num_unit}/{area_unit}",
             "needs_volume": any_volume and any(not np.isfinite(r["Volume"]) for r in out)}
    return out, units


def _num(v, default: float = np.nan) -> float:
    try:
        f = float(str(v).replace(",", ".")) if txt(v) else default
    except ValueError:
        return default
    return f if np.isfinite(f) else default
