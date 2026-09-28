export type ManagedModel = {
  id: string
  label: string
  artifact: string
  revision: string
  runtime: string
  lifecycle: string
  availability: string
  enabled: boolean
  qualified: boolean
  selectable: boolean
}

export type DeploymentStatus = {
  id: string
  state: string
  runtime: string
  detail?: string
  port_available?: boolean | null
}

export type Snapshot = {
  connected: boolean
  cluster?: string
  deployments: DeploymentStatus[]
  gpu_memory: { total: unknown; used: unknown; free: unknown }
  reported_at: string
}

export type Operation = {
  id: string
  action: string
  state: 'queued' | 'running' | 'succeeded' | 'failed'
  request: Record<string, string>
  submitted_at: string
  started_at?: string
  finished_at?: string
  error?: string
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...init,
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  let body: any = {}
  try { body = await response.json() } catch { /* non-JSON errors are handled below */ }
  if (!response.ok) {
    throw new ApiError(typeof body.detail === 'string' ? body.detail : 'The request could not be completed.', response.status)
  }
  return body as T
}

export const api = {
  async signIn(username: string, password: string) {
    return call<{ authenticated: true }>('/api/session', { method: 'POST', body: JSON.stringify({ username, password }) })
  },
  async signOut() {
    return call<{ authenticated: false }>('/api/session', { method: 'DELETE' })
  },
  models: () => call<{ models: ManagedModel[]; reported_at: string }>('/api/models'),
  status: () => call<Snapshot>('/api/status'),
  operations: () => call<{ operations: Operation[] }>('/api/operations'),
  operation: (id: string) => call<Operation>(`/api/operations/${encodeURIComponent(id)}`),
  action: (body: Record<string, string>) => call<{ operation: Operation }>('/api/actions', { method: 'POST', body: JSON.stringify(body) }),
}
