export type CalibrationColumnType = 'text' | 'number' | 'checkbox'

export interface CalibrationColumn {
  key: string
  label: string
  type: CalibrationColumnType
}

export type CalibrationRow = Record<string, string | number | boolean | null>

// Amperometry / ΔI-based instruments (existing Streamlit shape, ported verbatim)
export const AMPEROMETRY_COLUMNS: CalibrationColumn[] = [
  { key: 'Label', label: 'Label', type: 'text' },
  { key: 'Concentration', label: 'Concentration', type: 'number' },
  { key: 'Spike Vol', label: 'Spike Vol', type: 'number' },
  { key: 'Stock Conc', label: 'Stock Conc', type: 'number' },
  { key: 't_start', label: 't start (s)', type: 'number' },
  { key: 't_end', label: 't end (s)', type: 'number' },
  { key: 'avg_duration', label: 'Avg window (s)', type: 'number' },
  { key: 'Baseline', label: 'Baseline?', type: 'checkbox' },
]

// Solid-state (potentiometric) — no Baseline/blank-subtraction; adds a
// nullable direct-entry Reading_mV column. See backend/app/analysis/
// solid_state.py for why this diverges from the amperometric shape.
export const SOLID_STATE_COLUMNS: CalibrationColumn[] = [
  { key: 'Label', label: 'Label', type: 'text' },
  { key: 'Concentration', label: 'Concentration', type: 'number' },
  { key: 't_start', label: 't start (s)', type: 'number' },
  { key: 't_end', label: 't end (s)', type: 'number' },
  { key: 'avg_duration', label: 'Avg window (s)', type: 'number' },
  { key: 'Reading_mV', label: 'Reading (mV)', type: 'number' },
]
