import type { Automation, AutomationInput, Recipient, Run, Session } from './types'

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

let csrfToken = ''

async function call<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.method && init.method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}),
      ...init.headers,
    },
  })
  let body: any = {}
  try { body = await response.json() } catch { /* fixed fallback below */ }
  if (!response.ok) {
    throw new ApiError(typeof body.detail === 'string' ? body.detail : 'The request could not be completed.', response.status)
  }
  return body as T
}

export const api = {
  async signIn(username: string, password: string) {
    const response = await call<Session>('/api/session', { method: 'POST', body: JSON.stringify({ username, password }) })
    csrfToken = response.csrf_token || ''
    return response
  },
  async session() {
    const response = await call<Session>('/api/session')
    csrfToken = response.csrf_token || ''
    return response
  },
  async signOut() {
    const response = await call<{ authenticated: false }>('/api/session', { method: 'DELETE' })
    csrfToken = ''
    return response
  },
  automations: async () => (await call<{ automations: Automation[] }>('/api/automations')).automations,
  recipients: async () => (await call<{ recipients: Recipient[] }>('/api/recipients')).recipients,
  runs: async (id: string) => (await call<{ runs: Run[] }>(`/api/automations/${encodeURIComponent(id)}/runs`)).runs,
  create: async (item: AutomationInput) => (await call<{ automation: Automation }>('/api/automations', { method: 'POST', body: JSON.stringify(item) })).automation,
  update: async (id: string, item: AutomationInput) => (await call<{ automation: Automation }>(`/api/automations/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(item) })).automation,
  delete: (id: string) => call<{ deleted: boolean }>(`/api/automations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
