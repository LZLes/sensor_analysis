import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

const INSTRUMENT_TYPES = ['amperometry', 'solid_state', 'cv', 'assay'] as const

export function ProjectsPage() {
  const queryClient = useQueryClient()
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })
  const [name, setName] = useState('')
  const [instrumentType, setInstrumentType] = useState<(typeof INSTRUMENT_TYPES)[number]>('amperometry')

  const createProject = useMutation({
    mutationFn: () => api.createProject({ name, instrument_type: instrumentType }),
    onSuccess: () => {
      setName('')
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  return (
    <div>
      <h1>Sensor Analysis Studio</h1>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) createProject.mutate()
        }}
      >
        <input
          placeholder="Project name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select value={instrumentType} onChange={(e) => setInstrumentType(e.target.value as typeof instrumentType)}>
          {INSTRUMENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button type="submit">New project</button>
      </form>

      {projectsQuery.isLoading && <p>Loading…</p>}
      {projectsQuery.isError && <p>Failed to load projects — are you signed in?</p>}
      <ul>
        {projectsQuery.data?.map((p) => (
          <li key={p.id}>
            <Link to={`/projects/${p.id}`}>
              {p.name} ({p.instrument_type})
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
