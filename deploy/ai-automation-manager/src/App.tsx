import { useEffect, useMemo, useState } from 'react'
import { Activity, Archive, Bell, BookOpen, Check, ChevronDown, CircleHelp, Clock3, FilePlus2, Filter, Inbox, LoaderCircle, LogOut, MoreHorizontal, Pencil, Plus, Search, Settings2, ShieldCheck, Sparkles, Trash2, X } from 'lucide-react'
import { ApiError, api } from './api'
import { Automation, AutomationInput, CompletionRule, dayNames, ItemStatus, NotifyWhen, Run, Session, UpdateStyle, updateStyleLabels, notifyWhenLabels, completionRuleLabels } from './types'

type FilterStatus = 'All items' | 'Active' | 'Drafts' | 'Resolved' | 'Archived'
const statusFilters: FilterStatus[] = ['All items', 'Active', 'Drafts', 'Resolved', 'Archived']
const statusColor: Record<ItemStatus, string> = { Ready: 'green', Draft: 'gray', Resolved: 'blue', Archived: 'gray' }
const initialInput = (): AutomationInput => ({
  name: '', task_description: '', current_context: '', success_criteria: '', status: 'Draft', cadence: 'time_slot',
  days_to_run: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], time_to_run: 'Morning',
  update_style: 'briefing', notify_when: 'every_run', completion_rule: 'manual_archive', project: '', tag: '', recipients: [],
})

function displayDate(value: string | null | undefined) {
  if (!value) return 'Never run'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function cadenceLabel(item: Pick<Automation, 'cadence' | 'time_to_run'>) {
  return item.cadence === 'hourly' ? 'Hourly' : item.time_to_run === 'Morning' ? 'Daily · 07:00' : 'Daily · 21:00'
}

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [items, setItems] = useState<Automation[]>([])
  const [recipients, setRecipients] = useState<{ id: string; name: string }[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [filter, setFilter] = useState<FilterStatus>('All items')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Automation | 'new' | null>(null)
  const [draft, setDraft] = useState<AutomationInput>(initialInput())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [showAllRuns, setShowAllRuns] = useState(false)

  const selected = items.find(item => item.id === selectedId) ?? null
  const visibleItems = useMemo(() => items.filter(item => {
    const statusMatch = filter === 'All items' || (filter === 'Active' ? item.status === 'Ready' : item.status === filter.slice(0, -1) || item.status === filter)
    const q = query.trim().toLocaleLowerCase()
    return statusMatch && (!q || `${item.name} ${item.task_description} ${item.project} ${item.tag}`.toLocaleLowerCase().includes(q))
  }), [items, filter, query])

  async function loadData(keepSelection = true) {
    const nextItems = await api.automations()
    const nextRecipients = await api.recipients().catch(() => [])
    setItems(nextItems)
    setRecipients(nextRecipients)
    setSelectedId(current => keepSelection && current && nextItems.some(item => item.id === current) ? current : nextItems[0]?.id ?? null)
  }

  useEffect(() => {
    api.session().then(async state => { setSession(state); await loadData() }).catch(() => setSession({ authenticated: false }))
  }, [])

  useEffect(() => {
    if (!selectedId || !session?.authenticated) { setRuns([]); return }
    setShowAllRuns(false)
    api.runs(selectedId).then(setRuns).catch(err => setError(err.message))
  }, [selectedId, session?.authenticated])

  async function signIn(event: React.FormEvent) {
    event.preventDefault(); setError(''); setBusy(true)
    try { const state = await api.signIn(username, password); setSession(state); await loadData(false) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not sign in.') }
    finally { setBusy(false) }
  }

  async function signOut() {
    try { await api.signOut() } catch { /* session expires server-side */ }
    setSession({ authenticated: false }); setItems([]); setSelectedId(null)
  }

  function openNew() { setDraft(initialInput()); setEditing('new'); setError('') }
  function openEdit(item: Automation) {
    setDraft({ name: item.name, task_description: item.task_description, current_context: item.current_context, success_criteria: item.success_criteria,
      status: item.status, cadence: item.cadence, days_to_run: [...item.days_to_run], time_to_run: item.time_to_run, update_style: item.update_style,
      notify_when: item.notify_when, completion_rule: item.completion_rule, project: item.project, tag: item.tag, recipients: [...item.recipients] })
    setEditing(item); setError('')
  }

  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const saved = editing === 'new' ? await api.create(draft) : await api.update(editing!.id, draft)
      await loadData(); setSelectedId(saved.id); setEditing(null); setNotice(editing === 'new' ? 'Automation created' : 'Changes saved')
      window.setTimeout(() => setNotice(''), 3000)
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save automation.') }
    finally { setBusy(false) }
  }

  async function removeSelected() {
    if (!selected) return
    if (!window.confirm(`Delete “${selected.name}” and its run history?`)) return
    setBusy(true)
    try { await api.delete(selected.id); await loadData(false); setNotice('Automation deleted'); window.setTimeout(() => setNotice(''), 3000) }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not delete automation.') }
    finally { setBusy(false); setMenuOpen(false) }
  }

  if (!session) return <div className="loading-screen"><LoaderCircle className="spin" size={24} /> Loading your workspace…</div>
  if (!session.authenticated) return <main className="signin-page"><section className="signin-card"><div className="brand-mark"><Sparkles size={20} /></div><p className="eyebrow">HOMECOMPUTE · PRIVATE</p><h1>Your updates,<br />in one place.</h1><p className="signin-copy">Manage the briefings and system checks delivered through your Home Assistant Inbox.</p><form onSubmit={signIn} className="signin-form"><label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label><label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>{error && <p className="form-error">{error}</p>}<button className="primary-button full" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}<span>→</span></button></form><div className="signin-foot"><ShieldCheck size={15} /> Private to your home network</div></section><div className="signin-art"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-center"><Sparkles size={38}/></div><div className="art-label">A little more signal.<br/>A lot less noise.</div></div></main>

  return <div className="app-shell">
    <aside className="rail"><div className="rail-logo"><Sparkles size={20}/></div><button className="rail-icon active" title="Automations"><Activity size={19}/></button><button className="rail-icon" title="Inbox"><Inbox size={19}/></button><button className="rail-icon" title="Help"><CircleHelp size={19}/></button><div className="rail-bottom"><button className="rail-icon" title="Settings"><Settings2 size={19}/></button><button className="avatar" title={session.username}>{session.username?.slice(0,1).toUpperCase()}</button></div></aside>
    <aside className="sidebar"><div className="workspace-label"><div className="workspace-avatar">H</div><div><strong>HomeCompute</strong><small>Personal workspace</small></div><ChevronDown size={15}/></div><div className="nav-caption">WORKSPACE</div><button className="nav-item selected"><Activity size={16}/> Automations <span>{items.filter(i => i.status === 'Ready').length}</span></button><button className="nav-item muted" onClick={() => setNotice('Delivery is handled in your Home Assistant Inbox.')}><Inbox size={16}/> Delivery history</button><div className="sidebar-rule"/><div className="section-heading"><span>YOUR AUTOMATIONS</span><button onClick={openNew} aria-label="Create automation"><Plus size={15}/></button></div>{items.filter(i => i.status === 'Ready').slice(0,5).map(item => <button key={item.id} className={`mini-item ${selectedId === item.id ? 'chosen' : ''}`} onClick={() => setSelectedId(item.id)}><span className={`mini-dot ${item.cadence === 'hourly' ? 'dot-hourly' : ''}`}/><span>{item.name}</span></button>)}{!items.some(i => i.status === 'Ready') && <p className="empty-mini">Ready items will appear here.</p>}<div className="sidebar-spacer"/><div className="sidebar-note"><div className="note-icon"><BookOpen size={15}/></div><div><strong>Keep your signal clear</strong><p>Small, focused updates make it easier to see what matters.</p></div></div><button className="account-button" onClick={signOut}><LogOut size={15}/> Sign out</button></aside>
    <main className="main-pane"><header className="topbar"><div className="breadcrumbs"><span>Workspace</span><span className="crumb-sep">/</span><strong>Automations</strong></div><div className="top-actions"><span className="private-label"><span/> Private workspace</span><button className="primary-button" onClick={openNew}><Plus size={16}/> New automation</button></div></header>
      <div className="page-content"><div className="page-heading"><div><p className="eyebrow">YOUR PERSONAL SIGNAL</p><h1>Automations</h1><p className="page-subtitle">Briefings, topics and system checks—organized around what matters to you.</p></div><div className="summary-pill"><span className="summary-pulse"/><strong>{items.filter(i=>i.status==='Ready').length}</strong> active</div></div>
        <section className="overview-strip"><div className="overview-stat"><span className="stat-icon blue"><Activity size={16}/></span><div><strong>{items.filter(i=>i.status==='Ready').length}</strong><span>Running</span></div></div><div className="overview-separator"/><div className="overview-stat"><span className="stat-icon green"><Check size={16}/></span><div><strong>{items.filter(i=>i.status==='Resolved').length}</strong><span>Resolved</span></div></div><div className="overview-separator"/><div className="overview-stat"><span className="stat-icon amber"><Clock3 size={16}/></span><div><strong>{items.filter(i=>i.cadence==='hourly' && i.status==='Ready').length}</strong><span>Hourly checks</span></div></div><div className="overview-end"><span><Bell size={14}/> Sent through Home Assistant</span></div></section>
        <div className="content-grid"><section className="list-panel"><div className="list-toolbar"><div className="filter-tabs">{statusFilters.map(tab=><button key={tab} onClick={()=>setFilter(tab)} className={filter===tab?'tab active':'tab'}>{tab}{tab==='All items'&&<span>{items.length}</span>}</button>)}</div><div className="search-wrap"><Search size={15}/><input placeholder="Search automations" value={query} onChange={e=>setQuery(e.target.value)}/><button title="Filters"><Filter size={14}/></button></div></div>
          <div className="list-head"><span>AUTOMATION</span><span>SCHEDULE</span><span>STATUS</span><span>LAST RUN</span><span/></div>
          <div className="automation-list">{visibleItems.map(item=><button key={item.id} className={`automation-row ${selectedId===item.id?'row-selected':''}`} onClick={()=>setSelectedId(item.id)}><span className="item-title-cell"><span className={`item-symbol ${item.update_style}`}><Sparkles size={15}/></span><span className="item-title-copy"><strong>{item.name}</strong><small>{item.project || item.tag || item.task_description}</small></span></span><span className="schedule-cell"><Clock3 size={14}/>{cadenceLabel(item)}</span><span><span className={`status-badge ${statusColor[item.status]}`}><i/>{item.status}</span></span><span className="last-run-cell">{displayDate(item.last_run_at)}</span><MoreHorizontal className="row-more" size={17}/></button>)}{visibleItems.length===0&&<div className="empty-list"><div className="empty-illustration"><Activity size={22}/></div><h3>{items.length?'No matching automations':'Start with one useful update'}</h3><p>{items.length?'Try another search or status filter.':'Create a topic briefing, a daily news roundup, or a system check.'}</p>{items.length===0&&<button className="primary-button" onClick={openNew}><Plus size={15}/> Create automation</button>}</div>}</div>
          <div className="list-footer"><span>Showing {visibleItems.length} of {items.length} automations</span><span><span className="footer-dot"/> Synced just now</span></div>
        </section>
        <aside className="detail-panel">{selected?<><div className="detail-top"><div className="detail-type"><span className={`item-symbol ${selected.update_style}`}><Sparkles size={15}/></span><span>{selected.project || selected.tag || 'PERSONAL'}</span><div className="more-wrap"><button className="icon-button" onClick={()=>setMenuOpen(v=>!v)}><MoreHorizontal size={19}/></button>{menuOpen&&<div className="menu-pop"><button onClick={()=>openEdit(selected)}><Pencil size={14}/> Edit automation</button><button className="danger-text" onClick={removeSelected}><Trash2 size={14}/> Delete</button></div>}</div></div><span className={`status-badge ${statusColor[selected.status]}`}><i/>{selected.status}</span><h2>{selected.name}</h2><p className="detail-description">{selected.task_description}</p><button className="edit-link" onClick={()=>openEdit(selected)}><Pencil size={13}/> Edit details</button></div>
          <div className="detail-section"><div className="detail-label"><span>SUCCESS CRITERIA</span><span className="optional-label">{selected.completion_rule==='manual_archive'?'OPTIONAL':'RESOLUTION RULE'}</span></div><div className="criteria-box"><span className="criteria-check"><Check size={13}/></span><p>{selected.success_criteria || 'No success criteria. This item will run until you archive it.'}</p></div></div>
          {selected.current_context&&<div className="detail-section"><div className="detail-label"><span>WORKING CONTEXT</span><span className="optional-label">MARKDOWN</span></div><p className="context-preview">{selected.current_context}</p></div>}
          <div className="detail-section"><div className="detail-label"><span>SCHEDULE</span><button className="tiny-edit" onClick={()=>openEdit(selected)}><Pencil size={12}/> Edit</button></div><div className="schedule-card"><div className="schedule-main"><span className="schedule-icon"><Clock3 size={16}/></span><div><strong>{selected.cadence==='hourly'?'Every hour':selected.time_to_run==='Morning'?'Morning briefing':'Evening briefing'}</strong><small>{selected.cadence==='hourly'?'At the top of each hour':selected.time_to_run==='Morning'?'07:00':'21:00'} · Europe/Copenhagen</small></div></div><div className="weekday-chips">{dayNames.map((d,i)=><span key={d} className={selected.days_to_run.includes(d)?'weekday on':'weekday'}>{['M','T','W','T','F','S','S'][i]}</span>)}</div></div></div>
          <div className="detail-section"><div className="detail-label"><span>DELIVERY</span></div><div className="delivery-card"><span className="delivery-icon"><Inbox size={16}/></span><div><strong>Home Assistant Inbox</strong><small>{selected.recipient_names.length?selected.recipient_names.join(', '):'No current recipient match'}</small></div><span className="delivery-live"/></div><div className="delivery-options"><span>{updateStyleLabels[selected.update_style]}</span><span>{notifyWhenLabels[selected.notify_when]}</span></div></div>
          <div className="detail-section run-section"><div className="detail-label"><span>{showAllRuns?'RUN HISTORY':'RECENT RUNS'}</span><button className="tiny-edit" onClick={()=>setShowAllRuns(v=>!v)}>{runs.length>3?(showAllRuns?'Show recent':'View all'):''}</button></div>{runs.slice(0,showAllRuns?50:3).map(run=><div className="run-row" key={run.id}><span className={`run-dot ${run.state}`}/><div><strong>{run.state==='delivered'?'Update delivered':run.state==='skipped'?'No notification sent':run.state==='failed'?'Run failed':run.state==='delivery_pending'?'Delivery pending':'In progress'}</strong><small>{displayDate(run.finished_at || run.started_at)}</small></div>{run.state==='delivered'&&<Check size={14} className="run-check"/>}</div>)}{runs.length===0&&<p className="no-runs">No runs yet. This item will appear here after its first scheduled update.</p>}</div>
        </>:<div className="detail-empty"><div className="empty-illustration"><Sparkles size={22}/></div><h3>Choose an automation</h3><p>Select an item to see its task, schedule and delivery settings.</p></div>}</aside></div>
        <footer className="page-footer"><span>HomeCompute · AI automations</span><span><ShieldCheck size={13}/> Your data stays on your home network</span></footer>
      </div>
    </main>
    {notice&&<div className="toast"><Check size={15}/>{notice}<button onClick={()=>setNotice('')}><X size={14}/></button></div>}
    {editing&&<div className="modal-backdrop" role="presentation" onMouseDown={e=>{if(e.target===e.currentTarget)setEditing(null)}}><form className="editor-modal" onSubmit={save}><div className="modal-head"><div><p className="eyebrow">{editing==='new'?'NEW AUTOMATION':'EDIT AUTOMATION'}</p><h2>{editing==='new'?'Create an automation':'Edit automation'}</h2></div><button type="button" className="icon-button" onClick={()=>setEditing(null)}><X size={19}/></button></div><div className="modal-body">
        <label className="field-label">Name<input autoFocus maxLength={160} value={draft.name} onChange={e=>setDraft({...draft,name:e.target.value})} placeholder="e.g. AI and robotics briefing" required/></label>
        <label className="field-label">Task description<textarea maxLength={12000} rows={4} value={draft.task_description} onChange={e=>setDraft({...draft,task_description:e.target.value})} placeholder="What should this automation keep you updated on? Include the level of detail you want." required/></label>
        <label className="field-label">Working context <span>Updated after each run</span><textarea maxLength={24000} rows={3} value={draft.current_context} onChange={e=>setDraft({...draft,current_context:e.target.value})} placeholder="Current notes or context for the next update (optional)"/></label>
        <label className="field-label">Success criteria <span>{draft.completion_rule==='success_criteria'?'Required to resolve automatically':'Optional for items you’ll archive yourself'}</span><textarea maxLength={6000} rows={2} value={draft.success_criteria} onChange={e=>setDraft({...draft,success_criteria:e.target.value})} placeholder="When should this be marked resolved?" required={draft.completion_rule==='success_criteria'}/></label>
        <div className="two-fields"><label className="field-label">Status<select value={draft.status} onChange={e=>setDraft({...draft,status:e.target.value as ItemStatus})}><option>Draft</option><option>Ready</option><option>Resolved</option><option>Archived</option></select></label><label className="field-label">Project / topic<input maxLength={120} value={draft.project} onChange={e=>setDraft({...draft,project:e.target.value})} placeholder="Optional"/></label></div>
        <fieldset className="field-group"><legend>Schedule</legend><div className="choice-row"><label className={`choice-card ${draft.cadence==='time_slot'?'chosen':''}`}><input type="radio" name="cadence" checked={draft.cadence==='time_slot'} onChange={()=>setDraft({...draft,cadence:'time_slot',time_to_run:draft.time_to_run||'Morning'})}/><Clock3 size={15}/><span><strong>Morning or evening</strong><small>07:00 or 21:00</small></span></label><label className={`choice-card ${draft.cadence==='hourly'?'chosen':''}`}><input type="radio" name="cadence" checked={draft.cadence==='hourly'} onChange={()=>setDraft({...draft,cadence:'hourly',time_to_run:null})}/><Activity size={15}/><span><strong>Hourly</strong><small>At the top of each hour</small></span></label></div>{draft.cadence==='time_slot'&&<div className="slot-toggle"><button type="button" className={draft.time_to_run==='Morning'?'on':''} onClick={()=>setDraft({...draft,time_to_run:'Morning'})}>Morning · 07:00</button><button type="button" className={draft.time_to_run==='Evening'?'on':''} onClick={()=>setDraft({...draft,time_to_run:'Evening'})}>Evening · 21:00</button></div>}<div className="weekday-select">{dayNames.map((day,i)=><button type="button" key={day} className={draft.days_to_run.includes(day)?'day-on':''} onClick={()=>setDraft({...draft,days_to_run:draft.days_to_run.includes(day)?draft.days_to_run.filter(d=>d!==day):[...draft.days_to_run,day]})} title={day}>{['M','T','W','T','F','S','S'][i]}</button>)}</div><p className="field-hint">Times use Europe/Copenhagen. Selected days apply to every schedule.</p>{draft.days_to_run.length===0&&<p className="inline-warning">Select at least one weekday.</p>}</fieldset>
        <div className="two-fields"><label className="field-label">Update style<select value={draft.update_style} onChange={e=>setDraft({...draft,update_style:e.target.value as UpdateStyle})}>{Object.entries(updateStyleLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><label className="field-label">Notify me<select value={draft.notify_when} onChange={e=>setDraft({...draft,notify_when:e.target.value as NotifyWhen})}>{Object.entries(notifyWhenLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label></div>
        <label className="field-label">When should it stop?<select value={draft.completion_rule} onChange={e=>setDraft({...draft,completion_rule:e.target.value as CompletionRule})}>{Object.entries(completionRuleLabels).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        <fieldset className="field-group"><legend>Send to</legend><p className="field-hint recipient-hint">People are loaded from Home Assistant’s active Inbox recipients.</p>{recipients.length?recipients.map(person=><label className="recipient-choice" key={person.id}><input type="checkbox" checked={draft.recipients.includes(person.id)} onChange={e=>setDraft({...draft,recipients:e.target.checked?[...draft.recipients,person.id]:draft.recipients.filter(id=>id!==person.id)})}/><span className="recipient-avatar">{person.name.slice(0,1).toUpperCase()}</span><span>{person.name}</span><span className="recipient-check"><Check size={13}/></span></label>):<p className="inline-warning">No Home Assistant Inbox recipients are available right now.</p>}{recipients.length>0&&draft.recipients.length===0&&<p className="inline-warning">Select at least one recipient.</p>}</fieldset>
        {error&&<p className="form-error">{error}</p>}
      </div><div className="modal-foot"><button type="button" className="secondary-button" onClick={()=>setEditing(null)}>Cancel</button><button className="primary-button" disabled={busy}>{busy?<><LoaderCircle size={15} className="spin"/> Saving…</>:<><Check size={15}/> Save automation</>}</button></div></form></div>}
  </div>
}

export default App
