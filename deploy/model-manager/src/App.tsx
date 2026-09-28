import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertCircle, ArrowDown, Check, ChevronDown, CircleHelp, Cpu, Database,
  House, LoaderCircle, LockKeyhole, LogOut, MoreVertical, RefreshCw, Server,
  X,
} from 'lucide-react'
import { api, ApiError, type ManagedModel, type Operation, type Snapshot } from './api'

type Page = 'overview' | 'models' | 'activity'
type Panel = 'add' | 'replace' | null

function prettyArtifact(model?: ManagedModel) {
  if (!model) return 'Select an approved model'
  const slug = model.artifact.split('/').pop() || model.label
  return slug.replace(/-/g, ' ')
}

function displayTime(value?: string) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? '—' : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function memoryValue(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    if (value === 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    const unitIndex = value >= 1e12 ? 4 : value >= 1e9 ? 3 : value >= 1e6 ? 2 : value >= 1e3 ? 1 : 0
    return `${(value / Math.pow(1000, unitIndex)).toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
  }
  if (typeof value === 'string' && value.trim()) return value
  return '—'
}

function stateLabel(state?: string) {
  if (!state || state === 'unknown') return 'Unknown'
  return state.replace(/[_-]/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function isRunning(state?: string) {
  return Boolean(state && /^(running|ready|healthy|active|loaded|serving)$/i.test(state))
}

function isStopped(state?: string) {
  return Boolean(state && /^(stopped|inactive|unloaded|not.loaded|prepared|absent)$/i.test(state))
}

function App() {
  const [page, setPage] = useState<Page>('models')
  const [panel, setPanel] = useState<Panel>('add')
  const [models, setModels] = useState<ManagedModel[]>([])
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [operations, setOperations] = useState<Operation[]>([])
  const [loading, setLoading] = useState(true)
  const [auth, setAuth] = useState<'checking' | 'signed-out' | 'signed-in'>('checking')
  const [connectionError, setConnectionError] = useState('')
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [replaceFrom, setReplaceFrom] = useState('')
  const [replaceTo, setReplaceTo] = useState('')
  const [pendingAction, setPendingAction] = useState(false)
  const [replaceConfirm, setReplaceConfirm] = useState(false)
  const [loginError, setLoginError] = useState('')
  const [loginBusy, setLoginBusy] = useState(false)

  const modelById = useMemo(() => new Map(models.map((model) => [model.id, model])), [models])
  const statusById = useMemo(() => new Map((snapshot?.deployments || []).map((item) => [item.id, item])), [snapshot])
  const currentModels = models.filter((model) => isRunning(statusById.get(model.id)?.state))
  const selectedModel = modelById.get(selectedId)
  const replaceSource = modelById.get(replaceFrom)
  const replaceTarget = modelById.get(replaceTo)
  const inFlight = operations.find((operation) => operation.state === 'queued' || operation.state === 'running')

  const refresh = useCallback(async (initial = false) => {
    if (initial) setLoading(true)
    setConnectionError('')
    try {
      const modelResponse = await api.models()
      setAuth('signed-in')
      setModels(modelResponse.models)
      setSelectedId((current) => current && modelResponse.models.some((item) => item.id === current) ? current : modelResponse.models.find((item) => item.selectable)?.id || '')
      setReplaceTo((current) => current && modelResponse.models.some((item) => item.id === current && item.selectable) ? current : modelResponse.models.find((item) => item.selectable)?.id || '')
      const [statusResult, operationResult] = await Promise.allSettled([api.status(), api.operations()])
      if (statusResult.status === 'fulfilled') {
        setSnapshot(statusResult.value)
        const live = statusResult.value.deployments.filter((entry) => isRunning(entry.state))
        setReplaceFrom((current) => current && live.some((entry) => entry.id === current) ? current : live[0]?.id || '')
      } else {
        setConnectionError('Spark status is unavailable. The model catalog was received, but runtime state could not be read.')
      }
      if (operationResult.status === 'fulfilled') setOperations(operationResult.value.operations)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setAuth('signed-out')
        setModels([])
        setSnapshot(null)
      } else {
        setAuth('signed-in')
        setConnectionError(error instanceof Error ? error.message : 'The model manager service is unavailable.')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh(true) }, [refresh])

  useEffect(() => {
    if (auth !== 'signed-in' || inFlight) return
    const timer = window.setInterval(() => { void refresh() }, 15_000)
    return () => window.clearInterval(timer)
  }, [auth, inFlight, refresh])

  useEffect(() => {
    if (!operations.some((operation) => operation.state === 'queued' || operation.state === 'running')) return
    const timer = window.setInterval(async () => {
      try {
        const result = await api.operations()
        setOperations(result.operations)
        if (!result.operations.some((operation) => operation.state === 'queued' || operation.state === 'running')) {
          void refresh()
        }
      } catch { /* the main refresh will show loss of service */ }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [operations, refresh])

  const runAction = async (body: Record<string, string>, success: string) => {
    if (inFlight) {
      setActionError('Wait for the current model operation to finish before starting another.')
      return
    }
    setPendingAction(true)
    setActionError('')
    setNotice('')
    try {
      const response = await api.action(body)
      setOperations((previous) => [response.operation, ...previous])
      setNotice(success)
      setReplaceConfirm(false)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'The action could not be started.')
    } finally {
      setPendingAction(false)
    }
  }

  const signIn = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setLoginBusy(true)
    setLoginError('')
    try {
      await api.signIn(String(form.get('username') || ''), String(form.get('password') || ''))
      setAuth('signed-in')
      await refresh(true)
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : 'Sign in failed.')
    } finally {
      setLoginBusy(false)
    }
  }

  const signOut = async () => {
    try { await api.signOut() } catch { /* local state still clears */ }
    setAuth('signed-out')
    setModels([])
    setSnapshot(null)
  }

  const startReplace = () => {
    if (!replaceFrom || !replaceTo || replaceFrom === replaceTo || inFlight) return
    setReplaceConfirm(true)
  }

  const sortedModels = [...models].sort((a, b) => Number(isRunning(statusById.get(b.id)?.state)) - Number(isRunning(statusById.get(a.id)?.state)))

  if (auth === 'checking' && loading) return <div className="screen-loading"><LoaderCircle className="spin" size={24} /><span>Connecting to model manager…</span></div>

  if (auth === 'signed-out') return (
    <main className="login-screen">
      <form className="login-panel" onSubmit={signIn}>
        <div className="brand-mark"><Cpu size={21} strokeWidth={2.3} /></div>
        <h1>HomeCompute</h1>
        <p>Sign in to manage models on DGX Spark.</p>
        <label>Username<input name="username" autoComplete="username" required /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
        {loginError && <div className="inline-error"><AlertCircle size={15} />{loginError}</div>}
        <button className="primary-button login-submit" disabled={loginBusy}>
          {loginBusy ? <LoaderCircle size={16} className="spin" /> : <LockKeyhole size={16} />}
          Sign in
        </button>
      </form>
    </main>
  )

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Cpu size={21} strokeWidth={2.3} /></div><div><strong>HomeCompute</strong><span>DGX Spark Console</span></div></div>
        <nav aria-label="Main navigation">
          <button className={page === 'overview' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('overview')}><House size={20} />Overview</button>
          <button className={page === 'models' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('models')}><Database size={20} />Models</button>
          <button className={page === 'activity' ? 'nav-item active' : 'nav-item'} onClick={() => setPage('activity')}><Activity size={20} />Activity</button>
        </nav>
        <div className="sidebar-bottom"><span className="private-label">Private. On your hardware.</span><button className="signout" onClick={() => void signOut()}><LogOut size={15} />Sign out</button></div>
      </aside>

      <main className="main-area">
        <div className="page-header">
          <div><h1>{page === 'activity' ? 'Activity' : page === 'overview' ? 'Overview' : 'Models'}</h1><p>{page === 'activity' ? 'Recent model operations on your DGX Spark.' : page === 'overview' ? 'Model runtime status on your DGX Spark.' : 'Manage approved models on your DGX Spark.'}</p></div>
          {page !== 'activity' && <button className="primary-button add-top" onClick={() => { setPanel('add'); setPage('models') }}><span className="plus">+</span>Add model</button>}
          <button className="icon-button refresh-button" aria-label="Refresh status" disabled={Boolean(inFlight)} onClick={() => void refresh()}><RefreshCw size={17} /></button>
        </div>

        {connectionError && <div className="connection-banner"><AlertCircle size={17} /><span>{connectionError}</span><button onClick={() => void refresh()} aria-label="Retry"><RefreshCw size={15} /></button></div>}
        {snapshot?.deployments.some((entry) => entry.port_available === false) && <div className="connection-banner error-banner"><Server size={17} /><span>A managed vLLM port is already in use. Existing Compose workloads must be stopped before sparkrun can launch that model.</span></div>}
        {actionError && <div className="connection-banner error-banner"><AlertCircle size={17} /><span>{actionError}</span><button onClick={() => setActionError('')} aria-label="Dismiss error"><X size={15} /></button></div>}
        {notice && <div className="connection-banner success-banner"><Check size={17} /><span>{notice}</span><button onClick={() => setNotice('')} aria-label="Dismiss message"><X size={15} /></button></div>}

        {page === 'activity' ? (
          <section className="section-block activity-page">
            <h2>Recent operations</h2>
            <OperationsTable operations={operations} modelById={modelById} loading={loading} />
          </section>
        ) : (
          <>
            <section className="section-block models-section">
              <div className="section-heading-row"><h2>{page === 'overview' ? 'Runtime status' : 'Approved models'}</h2><span className="subtle-text">{snapshot?.reported_at ? `Updated ${displayTime(snapshot.reported_at)}` : 'Waiting for Spark status'}</span></div>
              <div className="table-frame model-table-frame">
                <table className="model-table">
                  <thead><tr><th>Model</th><th>State</th><th>Runtime</th><th className="actions-heading">Actions</th></tr></thead>
                  <tbody>
                    {loading && models.length === 0 ? <tr><td colSpan={4} className="empty-row"><LoaderCircle className="spin" size={17} />Loading approved models…</td></tr> :
                      sortedModels.length ? sortedModels.map((model) => {
                        const status = statusById.get(model.id)
                        const running = isRunning(status?.state)
                        const stopped = isStopped(status?.state)
                        return <tr key={model.id}>
                          <td><div className="model-name">{prettyArtifact(model)}</div><div className="model-alias">{model.label} <span className="model-id">· {model.id}</span></div></td>
                          <td><StatePill state={status?.state} running={running} approved={model.selectable} /></td>
                          <td className="runtime-cell">{model.runtime === 'vllm' ? 'vLLM' : model.runtime}</td>
                          <td className="row-actions">
                            {running ? <button className="secondary-button action-button" disabled={Boolean(inFlight) || pendingAction} onClick={() => void runAction({ action: 'unload', deployment: model.id }, `Unload requested for ${model.label}.`)}>Unload</button> :
                              stopped && model.selectable && status?.port_available !== false ? <button className="secondary-button action-button" disabled={Boolean(inFlight) || pendingAction} onClick={() => void runAction({ action: 'load', deployment: model.id }, `Load requested for ${model.label}.`)}>Load</button> :
                              stopped && model.selectable && status?.port_available === false ? <span className="not-approved">Port in use</span> :
                              !model.selectable ? <span className="not-approved">Not approved</span> : <span className="not-approved">{status?.state ? stateLabel(status.state) : 'Unknown'}</span>}
                            <button className="icon-button more-button" aria-label={`More actions for ${prettyArtifact(model)}`} onClick={() => { setSelectedId(model.id); setPanel('add') }}><MoreVertical size={18} /></button>
                          </td>
                        </tr>
                      }) : <tr><td colSpan={4} className="empty-row">No approved models were returned by the Spark adapter.</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="section-block memory-section">
              <div className="section-heading-row"><h2>GPU memory</h2><CircleHelp size={16} className="help-icon" aria-label="Memory reported by Sparkrun status" /></div>
              <div className="memory-frame">
                <MemoryStat label="Total memory" value={memoryValue(snapshot?.gpu_memory.total)} />
                <MemoryStat label="Used memory" value={memoryValue(snapshot?.gpu_memory.used)} />
                <MemoryStat label="Free memory" value={memoryValue(snapshot?.gpu_memory.free)} />
              </div>
            </section>

            <section className="section-block operations-section">
              <div className="section-heading-row"><h2>Recent operations</h2><button className="text-button" onClick={() => setPage('activity')}>View all</button></div>
              <OperationsTable operations={operations.slice(0, 4)} modelById={modelById} loading={loading} compact />
            </section>
          </>
        )}
      </main>

      {page !== 'activity' && panel && <aside className="inspector" aria-label={panel === 'add' ? 'Add model panel' : 'Replace model panel'}>
        <div className="inspector-header"><h2>{panel === 'add' ? 'Add model' : 'Replace model'}</h2><button className="icon-button" aria-label="Close panel" onClick={() => setPanel(null)}><X size={18} /></button></div>
        {panel === 'add' ? (
          <>
            <p className="inspector-intro">Select an approved model to prepare for use.</p>
            <label className="field-label" htmlFor="approved-model">Approved model</label>
            <div className="select-wrap"><select id="approved-model" value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              <option value="" disabled>{models.length ? 'Select an approved model' : 'No models returned'}</option>
              {models.filter((model) => model.selectable).map((model) => <option key={model.id} value={model.id}>{prettyArtifact(model)}</option>)}
            </select><ChevronDown size={17} /></div>
            {selectedModel ? <div className="model-facts">
              <Fact label="Model ID" value={selectedModel.id} />
              <Fact label="Runtime" value={selectedModel.runtime === 'vllm' ? 'vLLM' : selectedModel.runtime} />
              <Fact label="Revision" value={selectedModel.revision ? selectedModel.revision.slice(0, 12) : 'Pinned by catalog'} />
              <Fact label="Status" value={selectedModel.selectable ? 'Approved' : 'Unavailable'} />
            </div> : <div className="empty-selection">No approved deployment is available from the adapter.</div>}
            <div className="panel-rule" />
            <button className="primary-button full-button" disabled={!selectedModel || !selectedModel.selectable || pendingAction || Boolean(inFlight)} onClick={() => selectedModel && void runAction({ action: 'prepare', deployment: selectedModel.id }, `Preparation started for ${selectedModel.label}.`)}>
              {pendingAction || inFlight?.action === 'prepare' ? <LoaderCircle size={16} className="spin" /> : null}Prepare model
            </button>
            <div className="panel-rule replace-rule" />
            <h3>Replace model</h3>
            <p className="replace-copy">Unload the current model and activate an approved replacement.</p>
            <div className="replace-card">
              <div className="replace-label">Current model</div>
              <div className="replace-model-name">{replaceSource ? prettyArtifact(replaceSource) : 'No running model reported'}</div>
              {replaceSource && <div className="replace-model-id">{replaceSource.label}</div>}
              <ArrowDown className="replace-arrow" size={20} strokeWidth={1.8} />
              <label className="replace-label" htmlFor="replacement-model">Replacement model</label>
              <div className="select-wrap replace-select"><select id="replacement-model" value={replaceTo} onChange={(event) => setReplaceTo(event.target.value)}>
                <option value="" disabled>Select approved replacement</option>
                {models.filter((model) => model.selectable && model.id !== replaceFrom).map((model) => <option key={model.id} value={model.id}>{prettyArtifact(model)}</option>)}
              </select><ChevronDown size={16} /></div>
              {replaceTarget && <div className="replace-model-id target-alias">{replaceTarget.label}</div>}
              <div className="replace-buttons"><button className="primary-button replace-button" disabled={!replaceSource || !replaceTarget || replaceSource.id === replaceTarget.id || Boolean(inFlight) || pendingAction} onClick={startReplace}>Replace model</button><button className="secondary-button cancel-button" onClick={() => { setReplaceTo(''); setActionError('') }}>Cancel</button></div>
            </div>
            {currentModels.length > 1 && <div className="source-picker"><label className="field-label" htmlFor="current-model">Current model</label><div className="select-wrap"><select id="current-model" value={replaceFrom} onChange={(event) => setReplaceFrom(event.target.value)}>{currentModels.map((model) => <option key={model.id} value={model.id}>{prettyArtifact(model)}</option>)}</select><ChevronDown size={17} /></div></div>}
          </>
        ) : <ReplaceInspector models={models} currentModels={currentModels} replaceFrom={replaceFrom} replaceTo={replaceTo} replaceSource={replaceSource} replaceTarget={replaceTarget} setReplaceFrom={setReplaceFrom} setReplaceTo={setReplaceTo} onReplace={startReplace} inFlight={Boolean(inFlight)} />}
      </aside>}

      {replaceConfirm && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setReplaceConfirm(false) }}>
        <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-heading">
          <div className="dialog-icon"><RefreshCw size={20} /></div>
          <h2 id="confirm-heading">Replace running model?</h2>
          <p>This will unload <strong>{replaceSource ? prettyArtifact(replaceSource) : 'the current model'}</strong> and start <strong>{replaceTarget ? prettyArtifact(replaceTarget) : 'the replacement'}</strong>. Model availability may be interrupted while it starts.</p>
          <div className="dialog-actions"><button className="secondary-button" onClick={() => setReplaceConfirm(false)}>Keep current model</button><button className="primary-button" disabled={pendingAction} onClick={() => replaceSource && replaceTarget && void runAction({ action: 'replace', source: replaceSource.id, target: replaceTarget.id }, `Replacement started: ${replaceTarget.label}.`)}>Confirm replacement</button></div>
        </section>
      </div>}
    </div>
  )
}

function StatePill({ state, running, approved }: { state?: string; running: boolean; approved: boolean }) {
  const tone = running ? 'running' : isStopped(state) ? 'stopped' : approved ? 'unknown' : 'disabled'
  return <span className={`state-pill ${tone}`}><span className="state-dot" />{state ? stateLabel(state) : 'Unknown'}</span>
}

function MemoryStat({ label, value }: { label: string; value: string }) {
  return <div className="memory-stat"><span>{label}</span><strong>{value}</strong></div>
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="fact-row"><span>{label}</span><strong title={value}>{value}</strong></div>
}

function OperationsTable({ operations, modelById, loading, compact = false }: { operations: Operation[]; modelById: Map<string, ManagedModel>; loading: boolean; compact?: boolean }) {
  const deploymentName = (value?: string) => value ? prettyArtifact(modelById.get(value)) : '—'
  const rows = compact ? operations.slice(0, 4) : operations
  return <div className="table-frame operations-table-frame">
    <table className="operations-table"><thead><tr><th>Time</th><th>Operation</th><th>Model</th><th>Status</th></tr></thead>
      <tbody>{rows.length ? rows.map((operation) => {
        const deployment = operation.request.deployment || operation.request.to || operation.request.from
        return <tr key={operation.id}><td>{displayTime(operation.submitted_at)}</td><td className="operation-name">{operation.action === 'replace' ? 'Replace' : operation.action[0].toUpperCase() + operation.action.slice(1)}</td><td>{deploymentName(deployment)}</td><td><OperationState state={operation.state} error={operation.error} /></td></tr>
      }) : <tr><td colSpan={4} className="empty-row">{loading ? 'Loading recent operations…' : 'No recent operations.'}</td></tr>}</tbody>
    </table>
  </div>
}

function OperationState({ state, error }: { state: Operation['state']; error?: string }) {
  return <span className={`operation-state ${state}`} title={error || undefined}>{state === 'running' || state === 'queued' ? <LoaderCircle size={13} className="spin" /> : state === 'succeeded' ? <Check size={13} /> : state === 'failed' ? <AlertCircle size={13} /> : null}{state[0].toUpperCase() + state.slice(1)}</span>
}

function ReplaceInspector({ models, currentModels, replaceFrom, replaceTo, replaceSource, replaceTarget, setReplaceFrom, setReplaceTo, onReplace, inFlight }: {
  models: ManagedModel[]; currentModels: ManagedModel[]; replaceFrom: string; replaceTo: string; replaceSource?: ManagedModel; replaceTarget?: ManagedModel
  setReplaceFrom: (id: string) => void; setReplaceTo: (id: string) => void; onReplace: () => void; inFlight: boolean
}) {
  return <div className="replace-inspector">
    <p className="inspector-intro">Unload the current model and activate an approved replacement.</p>
    <label className="field-label" htmlFor="replace-source">Current model</label>
    <div className="select-wrap"><select id="replace-source" value={replaceFrom} onChange={(event) => setReplaceFrom(event.target.value)}><option value="" disabled>Select running model</option>{currentModels.map((model) => <option key={model.id} value={model.id}>{prettyArtifact(model)}</option>)}</select><ChevronDown size={17} /></div>
    <div className="replace-large-arrow"><ArrowDown size={22} /></div>
    <label className="field-label" htmlFor="replace-target">Replacement model</label>
    <div className="select-wrap"><select id="replace-target" value={replaceTo} onChange={(event) => setReplaceTo(event.target.value)}><option value="" disabled>Select approved replacement</option>{models.filter((model) => model.selectable && model.id !== replaceFrom).map((model) => <option key={model.id} value={model.id}>{prettyArtifact(model)}</option>)}</select><ChevronDown size={17} /></div>
    <div className="replace-summary">{replaceSource ? `${replaceSource.label} → ` : ''}{replaceTarget ? replaceTarget.label : 'Choose a replacement model'}</div>
    <button className="primary-button full-button" disabled={!replaceSource || !replaceTarget || inFlight} onClick={onReplace}>Replace model</button>
  </div>
}

export default App
