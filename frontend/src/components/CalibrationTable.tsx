import { useMemo } from 'react'
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import type { CalibrationColumn, CalibrationRow } from '../types/calibration'

interface CalibrationTableProps {
  columns: CalibrationColumn[]
  rows: CalibrationRow[]
  onChange: (rows: CalibrationRow[]) => void
}

/**
 * Editable calibration-points grid.
 *
 * Design note (see the migration plan, "Frontend data-grid choice"): every
 * cell is a plain controlled <input>/<input type="checkbox">, bound
 * directly to the `rows` state via onChange fired on every keystroke — NOT
 * on blur. This structurally avoids the commit-on-blur race that dropped a
 * just-typed edit in the original Streamlit app's data_editor (glide-data-
 * grid) when a compute button was clicked immediately after typing: there
 * is never a "value not yet committed to state" window here for a
 * click-elsewhere to expose, because the value is already in `rows` the
 * instant the keystroke fires, before any click handler on another
 * element could possibly run.
 */
export function CalibrationTable({ columns, rows, onChange }: CalibrationTableProps) {
  const columnHelper = createColumnHelper<CalibrationRow>()

  const tableColumns = useMemo(
    () =>
      columns.map((col) =>
        columnHelper.accessor((row) => row[col.key], {
          id: col.key,
          header: col.label,
          cell: (info) => {
            const rowIndex = info.row.index
            const value = info.getValue()

            const updateCell = (newValue: CalibrationRow[string]) => {
              const next = rows.slice()
              next[rowIndex] = { ...next[rowIndex], [col.key]: newValue }
              onChange(next)
            }

            if (col.type === 'checkbox') {
              return (
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  onChange={(e) => updateCell(e.target.checked)}
                  aria-label={`${col.label} row ${rowIndex + 1}`}
                />
              )
            }
            return (
              <input
                type={col.type === 'number' ? 'number' : 'text'}
                value={value === null || value === undefined ? '' : String(value)}
                onChange={(e) => {
                  const raw = e.target.value
                  if (col.type === 'number') {
                    updateCell(raw === '' ? null : Number(raw))
                  } else {
                    updateCell(raw)
                  }
                }}
                aria-label={`${col.label} row ${rowIndex + 1}`}
              />
            )
          },
        }),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [columns, rows],
  )

  const table = useReactTable({
    data: rows,
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
  })

  const addRow = () => {
    const blank: CalibrationRow = {}
    for (const col of columns) blank[col.key] = col.type === 'checkbox' ? false : null
    onChange([...rows, blank])
  }

  const removeRow = (index: number) => {
    onChange(rows.filter((_, i) => i !== index))
  }

  /**
   * Excel-paste support: paste a tab/newline-delimited block starting at
   * the focused cell, without needing a licensed grid library for it.
   */
  const handlePaste = (
    e: React.ClipboardEvent<HTMLTableSectionElement>,
    startRow: number,
    startColIndex: number,
  ) => {
    const text = e.clipboardData.getData('text/plain')
    if (!text.includes('\t') && !text.includes('\n')) return // let the default single-cell paste happen
    e.preventDefault()
    const grid = text
      .replace(/\r/g, '')
      .split('\n')
      .filter((line, i, arr) => !(i === arr.length - 1 && line === ''))
      .map((line) => line.split('\t'))

    const next = rows.slice()
    grid.forEach((lineValues, rOffset) => {
      const r = startRow + rOffset
      while (next.length <= r) {
        const blank: CalibrationRow = {}
        for (const col of columns) blank[col.key] = col.type === 'checkbox' ? false : null
        next.push(blank)
      }
      lineValues.forEach((cellValue, cOffset) => {
        const col = columns[startColIndex + cOffset]
        if (!col) return
        const parsed =
          col.type === 'number'
            ? cellValue.trim() === ''
              ? null
              : Number(cellValue)
            : col.type === 'checkbox'
              ? /^(true|1|yes)$/i.test(cellValue.trim())
              : cellValue
        next[r] = { ...next[r], [col.key]: parsed }
      })
    })
    onChange(next)
  }

  return (
    <div className="calibration-table-wrap">
      <table className="calibration-table">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => (
                <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>
              ))}
              <th />
            </tr>
          ))}
        </thead>
        <tbody
          onPaste={(e) => {
            const target = e.target as HTMLElement
            const td = target.closest('td')
            const tr = td?.closest('tr')
            if (!td || !tr) return
            const rowIndex = Array.from(tr.parentElement!.children).indexOf(tr)
            const colIndex = Array.from(tr.children).indexOf(td)
            handlePaste(e, rowIndex, colIndex)
          }}
        >
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
              <td>
                <button type="button" onClick={() => removeRow(row.index)} aria-label={`Remove row ${row.index + 1}`}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={addRow}>
        + Add row
      </button>
    </div>
  )
}
