import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CalibrationTable } from './CalibrationTable'
import { AMPEROMETRY_COLUMNS } from '../types/calibration'
import type { CalibrationRow } from '../types/calibration'

function Harness({ onCompute }: { onCompute: (rows: CalibrationRow[]) => void }) {
  const [rows, setRows] = useState<CalibrationRow[]>([
    { Label: 'Blank', Concentration: 0, t_start: 0, t_end: 50, Baseline: true },
    { Label: 'Step 1', Concentration: 1, t_start: 70, t_end: 110, Baseline: false },
  ])
  return (
    <div>
      <CalibrationTable columns={AMPEROMETRY_COLUMNS} rows={rows} onChange={setRows} />
      <button onClick={() => onCompute(rows)}>Compute Calibration</button>
    </div>
  )
}

describe('CalibrationTable', () => {
  it(
    'a cell edit reaches the compute handler even when the compute ' +
      'button is clicked without ever blurring the input first ' +
      '(the exact scenario that dropped edits in the old Streamlit grid)',
    () => {
      const onCompute = vi.fn()
      render(<Harness onCompute={onCompute} />)

      const tEndInput = screen.getByLabelText('t end (s) row 2')
      // Type a new value but do NOT blur/focus-away — go straight to the
      // click. A real click also fires its own focus/blur sequence on
      // real browsers, but jsdom's fireEvent.click does not, which is
      // exactly the failure mode we want to guard against: the compute
      // handler must see the edit from onChange state, not from some
      // blur-triggered commit that may or may not have happened yet.
      fireEvent.change(tEndInput, { target: { value: '500' } })
      fireEvent.click(screen.getByText('Compute Calibration'))

      expect(onCompute).toHaveBeenCalledTimes(1)
      const rowsSeenByCompute = onCompute.mock.calls[0][0] as CalibrationRow[]
      expect(rowsSeenByCompute[1].t_end).toBe(500)
    },
  )

  it('updates a checkbox cell immediately on click', () => {
    const onCompute = vi.fn()
    render(<Harness onCompute={onCompute} />)
    const baselineCheckbox = screen.getByLabelText('Baseline? row 2') as HTMLInputElement
    expect(baselineCheckbox.checked).toBe(false)
    fireEvent.click(baselineCheckbox)
    fireEvent.click(screen.getByText('Compute Calibration'))
    const rowsSeenByCompute = onCompute.mock.calls[0][0] as CalibrationRow[]
    expect(rowsSeenByCompute[1].Baseline).toBe(true)
  })

  it('adds and removes rows', () => {
    const onCompute = vi.fn()
    render(<Harness onCompute={onCompute} />)
    fireEvent.click(screen.getByText('+ Add row'))
    fireEvent.click(screen.getByText('Compute Calibration'))
    expect((onCompute.mock.calls[0][0] as CalibrationRow[]).length).toBe(3)

    fireEvent.click(screen.getByLabelText('Remove row 1'))
    fireEvent.click(screen.getByText('Compute Calibration'))
    expect((onCompute.mock.calls[1][0] as CalibrationRow[]).length).toBe(2)
  })

  it('pastes a tab/newline-delimited block starting at the pasted cell', () => {
    const onCompute = vi.fn()
    render(<Harness onCompute={onCompute} />)
    const concentrationCell = screen.getByLabelText('Concentration row 1')
    const pasteText = '2.5\t10\n5\t20'
    fireEvent.paste(concentrationCell.closest('td')!, {
      clipboardData: { getData: () => pasteText },
    })
    fireEvent.click(screen.getByText('Compute Calibration'))
    const rowsSeenByCompute = onCompute.mock.calls[0][0] as CalibrationRow[]
    expect(rowsSeenByCompute[0].Concentration).toBe(2.5)
    expect(rowsSeenByCompute[0]['Spike Vol']).toBe(10)
    expect(rowsSeenByCompute[1].Concentration).toBe(5)
    expect(rowsSeenByCompute[1]['Spike Vol']).toBe(20)
  })
})
