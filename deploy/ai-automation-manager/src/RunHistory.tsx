import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
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
    const automationId: string = key
    const controller = new AbortController()
    let timer: number
    let previous = new Set<string>()

    async function load() {
      try {
        const next = await api.runs(automationId, controller.signal)
        if (controller.signal.aborted) return

        if (next.some(row => previous.has(row.id) && !activeRun(row))) onSettled()
        previous = new Set(next.filter(activeRun).map(row => row.id))
        setResult({ key: automationId, runs: next, error: '' })
      } catch (err) {
        if (controller.signal.aborted) return
        setResult(current => ({
          key: automationId,
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

export function RunEntry({ run }: { run: Run }) {
  const when = run.started_at ?? run.scheduled_at
  const startLabel = dateFormatter.format(new Date(when))
  const finishLabel = run.finished_at ? dateFormatter.format(new Date(run.finished_at)) : null

  return <li className="run-row">
    <span className={`run-dot ${run.state}`} aria-hidden="true" />
    <div>
      <strong>{labels[run.state]}</strong>
      <small>{run.source === 'manual' ? 'Manual' : 'Scheduled'} · <time dateTime={when}>{startLabel}</time>
        {finishLabel && finishLabel !== startLabel && <> · Finished <time dateTime={run.finished_at!}>{finishLabel}</time></>}
      </small>
      {run.state === 'failed' && run.error_code && failureDetails[run.error_code] &&
        <small>{failureDetails[run.error_code]}</small>}
      {run.change_summary && <p className="run-summary">{run.change_summary}</p>}
    </div>
    {run.state === 'delivered' && <Check size={14} className="run-check" aria-hidden="true" />}
  </li>
}
