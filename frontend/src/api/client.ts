const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include', // send the session cookie set by /auth/google
    headers:
      options.body instanceof FormData
        ? options.headers
        : { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, detail.detail ?? res.statusText)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export interface User {
  id: string
  email: string
  name: string
  is_admin: boolean
}

export interface Project {
  id: string
  name: string
  instrument_type: 'amperometry' | 'cv' | 'assay' | 'solid_state'
  notes: string | null
  owner_id: string
  created_at: string
  updated_at: string
}

export interface Dataset {
  id: string
  project_id: string
  filename: string
  channel_mappings: { name: string; tc?: string; vc?: string; ic: string }[]
  calibration_table: Record<string, unknown>[]
  order_index: number
  uploaded_at: string
}

export interface CalibrationResult {
  id: string
  project_id: string
  fit_type: string
  n_segments: number | null
  results: Record<string, unknown>
  contributing_dataset_ids: string[]
  computed_at: string
  insights_text: string | null
}

export const api = {
  loginWithGoogle: (idToken: string) =>
    request<User>('/auth/google', { method: 'POST', body: JSON.stringify({ id_token: idToken }) }),
  logout: () => request<{ ok: true }>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),

  listProjects: () => request<Project[]>('/projects'),
  createProject: (body: { name: string; instrument_type: string; notes?: string }) =>
    request<Project>('/projects', { method: 'POST', body: JSON.stringify(body) }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  deleteProject: (id: string) => request<{ ok: true }>(`/projects/${id}`, { method: 'DELETE' }),

  listDatasets: (projectId: string) => request<Dataset[]>(`/projects/${projectId}/datasets`),
  uploadDataset: (projectId: string, file: File, fileFormat: 'standard' | 'multichannel', delimiter: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('file_format', fileFormat)
    form.append('delimiter', delimiter)
    return request<Dataset>(`/projects/${projectId}/datasets`, { method: 'POST', body: form })
  },
  updateCalibrationTable: (projectId: string, datasetId: string, calibrationTable: Record<string, unknown>[]) =>
    request<Dataset>(`/projects/${projectId}/datasets/${datasetId}/calibration-table`, {
      method: 'PATCH',
      body: JSON.stringify({ calibration_table: calibrationTable }),
    }),
  updateChannelMappings: (projectId: string, datasetId: string, channelMappings: Dataset['channel_mappings']) =>
    request<Dataset>(`/projects/${projectId}/datasets/${datasetId}/channel-mappings`, {
      method: 'PATCH',
      body: JSON.stringify(channelMappings),
    }),

  computeCalibration: (
    projectId: string,
    body: {
      dataset_channel_pairs: [string, string][]
      fit_type?: string
      n_segments?: number
      show_channel_average?: boolean
    },
  ) => request<CalibrationResult>(`/projects/${projectId}/calibration/compute`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  generateInsights: (projectId: string, resultId: string) =>
    request<CalibrationResult>(`/projects/${projectId}/calibration/${resultId}/insights`, { method: 'POST' }),
}
