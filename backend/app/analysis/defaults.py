"""Default (starter) calibration-table shapes, per instrument type — as
plain JSON-able records (list[dict]), since calibration_table is stored as
JSONB, not a typed DataFrame, once it leaves the analysis layer."""
from __future__ import annotations


def default_calibration_table(instrument_type: str) -> list[dict]:
    if instrument_type == "solid_state":
        # No Baseline/blank-subtraction for potentiometric sensors — see
        # backend/app/analysis/solid_state.py docstring for why. Reading_mV
        # is a nullable direct-entry column: if filled, it IS the
        # calibration point; if null, fall back to windowed averaging
        # against the imported trace (t_start/t_end/avg_duration).
        return [
            {"Label": "Std 1", "Concentration": 0.1, "t_start": None, "t_end": None,
             "avg_duration": None, "Reading_mV": None},
            {"Label": "Std 2", "Concentration": 1.0, "t_start": None, "t_end": None,
             "avg_duration": None, "Reading_mV": None},
            {"Label": "Std 3", "Concentration": 10.0, "t_start": None, "t_end": None,
             "avg_duration": None, "Reading_mV": None},
        ]
    # amperometry (and any future ΔI-based instrument type)
    return [
        {"Label": "Blank", "Concentration": 0.0, "Spike Vol": None, "Stock Conc": None,
         "t_start": 0.0, "t_end": 60.0, "avg_duration": None, "Baseline": True},
        {"Label": "Step 1", "Concentration": 0.1, "Spike Vol": None, "Stock Conc": None,
         "t_start": 120.0, "t_end": 180.0, "avg_duration": None, "Baseline": False},
        {"Label": "Step 2", "Concentration": 0.5, "Spike Vol": None, "Stock Conc": None,
         "t_start": 300.0, "t_end": 360.0, "avg_duration": None, "Baseline": False},
        {"Label": "Step 3", "Concentration": 1.0, "Spike Vol": None, "Stock Conc": None,
         "t_start": 480.0, "t_end": 540.0, "avg_duration": None, "Baseline": False},
    ]
