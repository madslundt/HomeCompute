import { useEffect, useState } from 'react'
import { Check, Inbox } from 'lucide-react'
import { api } from './api'
import type { Run } from './types'

const labels: Record<Run['state'], string> = {
  queued: 'Queued',
  running: 'In progress',
  delivery_pending: 'Delivery pending',
  delivered: 'Update delivered',
  skipped: 'No notification sent',
  failed: 'Run failed',
}

const failureDetails: Record<string, string> = {
  agent_iteration_limit: 'The research agent reached its iteration limit.',
  workflow_failed: 'The automation worker could not finish the update.',
  run_interrupted: 'The run did not finish within two hours.',
  item_not_ready: 'The automation was no longer Ready when the worker picked it up.',
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Copenhagen',
})

export function activeRun(run: Run) {
  return run.state === 'queued' || run.state === 'running'
}

export function useRunHistory(key: string | null, refresh: number, onSettled: () => void) {
  const [result, setResult] = useState<{ key: string; runs: Run[]; error: string }>()

  useEffect(() => {
    if (!key) return
    const controller = new AbortController()
    let timer: number
    let previous = new Set<string>()

    async function load() {
      try {
        const next = key === 'deliveries'
          ? await api.deliveryHistory(controller.signal)
          : await api.runs(key!, controller.signal)
        if (controller.signal.aborted) return

        if (next.some(row => previous.has(row.id) && !activeRun(row))) onSettled()
        previous = new Set(next.filter(activeRun).map(row => row.id))
        setResult({ key: key!, runs: next, error: '' })
      } catch (err) {
        if (controller.signal.aborted) return
        setResult(current => ({
          key: key!,
          runs: current?.key === key ? current.runs : [],
          error: err instanceof Error ? err.message : 'Could not load run history.',
        }))
      }
      timer = window.setTimeout(load, 5000)
    }

    void load()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [key, refresh, onSettled])

  return result?.key === key ? result : undefined
}

export function RunEntry({ run, openAutomation }: { run: Run; openAutomation?: (id: string) => void }) {
  const when = run.started_at ?? run.scheduled_at
  const startLabel = dateFormatter.format(new Date(when))
  const finishLabel = run.finished_at ? dateFormatter.format(new Date(run.finished_at)) : null

  return <li className="run-row">
    <span className={`run-dot ${run.state}`} aria-hidden="true" />
    <div>
      {openAutomation && <button className="run-automation-link" onClick={() => openAutomation(run.automation_id)}>
        {run.automation_name}
      </button>}
      <strong>{labels[run.state]}</strong>
      <small>{run.source === 'manual' ? 'Manual' : 'Scheduled'} · <time dateTime={when}>{startLabel}</time>
        {finishLabel && finishLabel !== startLabel && <> · Finished <time dateTime={run.finished_at!}>{finishLabel}</time></>}
      </small>
      {run.state === 'failed' && run.error_code && failureDetails[run.error_code] &&
        <small>{failureDetails[run.error_code]}</small>}
      {run.change_summary && <p className="run-summary">{run.change_summary}</p>}
      {run.delivery_receipt && <small className="run-receipt">Inbox receipt {run.delivery_receipt}</small>}
    </div>
    {run.state === 'delivered' && <Check size={14} className="run-check" aria-hidden="true" />}
  </li>
}

export function DeliveryHistory({ history, openAutomation }: {
  history: ReturnType<typeof useRunHistory>
  openAutomation: (id: string) => void
}) {
  return <>
    <div className="page-heading">
      <div><h1>Delivery history</h1><p className="page-subtitle">Recent automation runs and Home Assistant Inbox outcomes.</p></div>
    </div>
    <section className="list-panel delivery-history-panel" aria-label="Recent delivery history">
      {history?.error && <p className="inline-warning" role="alert">{history.error}</p>}
      {!history && <p className="no-runs" role="status">Loading delivery history…</p>}
      {history && !history.error && !history.runs.length && <div className="empty-list">
        <div className="empty-illustration"><Inbox size={22} /></div>
        <h3>No runs yet</h3><p>Scheduled and manual runs will appear here.</p>
      </div>}
      {history && history.runs.length > 0 && <ul className="run-list delivery-history-list">
        {history.runs.map(run => <RunEntry key={run.id} run={run} openAutomation={openAutomation} />)}
      </ul>}
    </section>
  </>
}
