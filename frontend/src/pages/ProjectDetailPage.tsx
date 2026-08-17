import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, type Dataset } from '../api/client'
import { CalibrationTable } from '../components/CalibrationTable'
import { AMPEROMETRY_COLUMNS, SOLID_STATE_COLUMNS } from '../types/calibration'
import type { CalibrationRow } from '../types/calibration'

export function ProjectDetailPage() {
  const { id: projectId } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const [activeDatasetId, setActiveDatasetId] = useState<string | null>(null)
  const [selectedChannels, setSelectedChannels] = useState<[string, string][]>([])

  const projectQuery = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: !!projectId,
  })
  const datasetsQuery = useQuery({
    queryKey: ['datasets', projectId],
    queryFn: () => api.listDatasets(projectId!),
    enabled: !!projectId,
  })

  const uploadDataset = useMutation({
    mutationFn: (file: File) => api.uploadDataset(projectId!, file, 'standard', ','),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasets', projectId] }),
  })

  const updateCalibrationTable = useMutation({
    mutationFn: (vars: { datasetId: string; rows: CalibrationRow[] }) =>
      api.updateCalibrationTable(projectId!, vars.datasetId, vars.rows),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['datasets', projectId] }),
  })

  const computeCalibration = useMutation({
    mutationFn: () => api.computeCalibration(projectId!, { dataset_channel_pairs: selectedChannels }),
  })

  const columns =
    projectQuery.data?.instrument_type === 'solid_state' ? SOLID_STATE_COLUMNS : AMPEROMETRY_COLUMNS

  const datasets: Dataset[] = datasetsQuery.data ?? []
  const activeDataset = datasets.find((d) => d.id === activeDatasetId) ?? datasets[0]

  return (
    <div>
      <h1>{projectQuery.data?.name}</h1>
      <p>Instrument: {projectQuery.data?.instrument_type}</p>

      <section>
        <h2>Datasets</h2>
        <input
          type="file"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) uploadDataset.mutate(file)
          }}
        />
        <ul>
          {datasets.map((d) => (
            <li key={d.id}>
              <button type="button" onClick={() => setActiveDatasetId(d.id)}>
                {d.filename}
              </button>
            </li>
          ))}
        </ul>
      </section>

      {activeDataset && (
        <section>
          <h2>Calibration table — {activeDataset.filename}</h2>
          <CalibrationTable
            columns={columns}
            rows={activeDataset.calibration_table as CalibrationRow[]}
            onChange={(rows) => updateCalibrationTable.mutate({ datasetId: activeDataset.id, rows })}
          />

          <h3>Channels</h3>
          {activeDataset.channel_mappings.map((ch) => (
            <label key={ch.name} style={{ display: 'block' }}>
              <input
                type="checkbox"
                checked={selectedChannels.some(([d, c]) => d === activeDataset.id && c === ch.name)}
                onChange={(e) => {
                  if (e.target.checked) {
                    setSelectedChannels([...selectedChannels, [activeDataset.id, ch.name]])
                  } else {
                    setSelectedChannels(
                      selectedChannels.filter(([d, c]) => !(d === activeDataset.id && c === ch.name)),
                    )
                  }
                }}
              />
              {ch.name}
            </label>
          ))}

          <button
            type="button"
            disabled={selectedChannels.length === 0}
            onClick={() => computeCalibration.mutate()}
          >
            Compute Calibration
          </button>
        </section>
      )}

      {computeCalibration.data && (
        <section>
          <h2>Results</h2>
          <pre>{JSON.stringify(computeCalibration.data.results, null, 2)}</pre>
        </section>
      )}
      {computeCalibration.isError && <p role="alert">{(computeCalibration.error as Error).message}</p>}
    </div>
  )
}
