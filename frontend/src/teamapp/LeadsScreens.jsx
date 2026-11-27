/**
 * EVOSYS PRO MOBILE - LEADS IN A PROGRAM (/app/p/<pid>/leads ...).
 *
 * The daily work a person used to open the website for:
 *   /app/p/<pid>/leads            search the program's leads, add a new one
 *   /app/p/<pid>/leads/<leadId>   one lead: call / text / email in one tap,
 *                                 move the stage, follow-ups (add, done),
 *                                 notes, and a text from EvoSys when allowed
 *
 * NOTHING NEW ON THE SERVER. Every call is an existing website route, pinned
 * to this program's workspace (programRequest), and the server decides scope
 * exactly as it does for the website: an advisor sees only their own leads,
 * a lead outside the workspace is a 404, a text goes through the same compose
 * gate (consent, do-not-contact, quiet hours, sender) and send path.
 *
 * Shown only while /team-app/me says previews.phone_work (owner preview until
 * the owner opens it to everyone - MOBILE_WORK_FOR_ALL on the server).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { formatPhone } from '../utils/phone'
import { ErrorNote, Icon, Loading, useTeam } from './TeamApp'
import { Frame, useEntry, useProgramGet } from './ProgramWorkspace'
import { ago, contactLinks, draftKey, dueLabel, followUpChoices, hasLeads, leadName, mobileKind,
         mobileProgramPath, newLeadProblem, parseTs, programRequest } from './programMobile'

const PAGE = 60

function errText(e, fallback) {
  const d = e && (e.detail || (e.data && e.data.detail))
  if (d && typeof d === 'object' && d.message) return d.message
  if (typeof d === 'string') return d
  return (e && e.message) || fallback
}

/** Stages of THIS workspace's pipeline (the same list the website's board uses). */
function useStages(item) {
  const { data } = useProgramGet(item, item ? '/leads/workspace-summary' : null)
  return useMemo(() => {
    const tiers = (data && Array.isArray(data.tiers)) ? data.tiers : []
    return tiers.map(t => ({ key: t.key, label: t.label || t.key }))
  }, [data])
}

function stageLabel(stages, key) {
  const s = stages.find(x => x.key === key)
  return s ? s.label : (key ? String(key).replace(/_/g, ' ') : '')
}

function NotOn({ entry }) {
  const navigate = useNavigate()
  return (
    <div className="ta-state">
      <p>Leads aren’t on the phone for this program yet.</p>
      <button type="button" className="ta-btn ta-btn--ghost" onClick={() => navigate(mobileProgramPath(entry.item))}>Back</button>
    </div>
  )
}

/** true / false, or null while /team-app/me is still loading (no flash of "not on yet"). */
function useWorkOn() {
  const { me } = useTeam()
  if (!me) return null
  return !!(me.previews && me.previews.phone_work)
}

// ── List ────────────────────────────────────────────────────────────────────

export function ProgramLeads() {
  const entry = useEntry()
  const work = useWorkOn()
  const item = entry.item
  return (
    <Frame entry={entry} tab="leads">
      {item && (work === null ? <Loading /> : work && hasLeads(item) ? <LeadsBody item={item} /> : <NotOn entry={entry} />)}
    </Frame>
  )
}

function LeadsBody({ item }) {
  const navigate = useNavigate()
  const stages = useStages(item)
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  const [adding, setAdding] = useState(false)
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 300); return () => clearTimeout(t) }, [q])
  useEffect(() => {
    let live = true
    setError(null)
    const params = new URLSearchParams({ sort: 'activity', page_size: String(PAGE) })
    if (term) params.set('search', term)
    api.get('/leads/?' + params.toString(), programRequest(item))
      .then(d => { if (live) setRows(Array.isArray(d) ? d : (d && (d.items || d.leads)) || []) })
      .catch(e => { if (live) { setRows([]); setError(e) } })
    return () => { live = false }
  }, [item, term, n])
  const open = l => navigate(mobileProgramPath(item, 'leads') + '/' + encodeURIComponent(l.id))
  return (
    <>
      <div className="ta-lead-tools">
        <label className="ta-searchbox">
          <Icon name="search" size={20} />
          <span className="ta-sr">Search leads</span>
          <input type="search" placeholder="Name, phone or email…" value={q} onChange={e => setQ(e.target.value)}
                 enterKeyHint="search" autoComplete="off" />
          {q && <button type="button" className="ta-icon-btn ta-icon-btn--sm" onClick={() => setQ('')} aria-label="Clear search"><Icon name="x" size={16} /></button>}
        </label>
        <button type="button" className="ta-compose" aria-label="New lead" onClick={() => setAdding(true)}><Icon name="plus" size={22} /></button>
      </div>
      {error && <ErrorNote error={error} onRetry={() => setN(x => x + 1)} />}
      {rows === null && !error && <Loading />}
      {rows && rows.length === 0 && !error && (
        <div className="ta-state">{term ? 'No lead matches “' + term + '”.' : 'No leads yet. Tap + to add one.'}</div>
      )}
      {rows && rows.length > 0 && (
        <ul className="ta-cards" aria-label="Leads" data-count={rows.length}>
          {rows.map(l => (
            <li key={l.id}>
              <button type="button" className="ta-leadrow" onClick={() => open(l)} data-lead={l.id}>
                <span className="ta-avatar ta-avatar--sm" aria-hidden>{(leadName(l)[0] || '?').toUpperCase()}</span>
                <span className="ta-chatrow-main">
                  <span className="ta-chatrow-top">
                    <span className="ta-chatrow-name">{leadName(l)}</span>
                    <span className="ta-chatrow-time">{l.last_messaged_at ? ago(l.last_messaged_at) : ''}</span>
                  </span>
                  <span className="ta-chatrow-preview">
                    {[stageLabel(stages, l.tier), formatPhone(l.phone) || l.email].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <Icon name="chev" size={20} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {rows && rows.length >= PAGE && !term && <p className="ta-muted ta-small">Showing the {PAGE} most recently active. Search to find anyone else.</p>}
      {adding && <NewLeadSheet item={item} stages={stages} onClose={() => setAdding(false)}
                               onCreated={id => navigate(mobileProgramPath(item, 'leads') + '/' + encodeURIComponent(id))} />}
    </>
  )
}

function NewLeadSheet({ item, stages, onClose, onCreated }) {
  const [f, setF] = useState({ first_name: '', last_name: '', phone: '', email: '', notes: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const set = k => e => setF(x => ({ ...x, [k]: e.target.value }))
  async function save(e) {
    e.preventDefault()
    const problem = newLeadProblem(f)
    if (problem) { setErr(problem); return }
    setBusy(true); setErr(null)
    try {
      const body = { first_name: f.first_name.trim(), last_name: f.last_name.trim(),
                     phone: f.phone.trim() || null, email: f.email.trim() || null, notes: f.notes.trim() || null }
      if (stages.length) body.tier = stages[0].key
      const r = await api.post('/leads/create', body, programRequest(item))
      onCreated(r.id)
    } catch (e2) { setErr(errText(e2, 'Could not add the lead. Try again.')) } finally { setBusy(false) }
  }
  return (
    <div className="ta-sheet" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <form className="ta-sheet-card" role="dialog" aria-modal="true" aria-labelledby="ta-newlead-h" onSubmit={save}>
        <div className="ta-sheet-grip" aria-hidden />
        <h2 id="ta-newlead-h" className="ta-sheet-title">New lead</h2>
        <p className="ta-muted ta-small">Added to {item.display}{stages.length ? ' as “' + stages[0].label + '”' : ''}. Nothing is sent.</p>
        <div className="ta-form2">
          <label className="ta-lfield"><span>First name</span><input value={f.first_name} onChange={set('first_name')} autoComplete="off" /></label>
          <label className="ta-lfield"><span>Last name</span><input value={f.last_name} onChange={set('last_name')} autoComplete="off" /></label>
        </div>
        <label className="ta-lfield"><span>Phone</span><input type="tel" inputMode="tel" value={f.phone} onChange={set('phone')} autoComplete="off" /></label>
        <label className="ta-lfield"><span>Email</span><input type="email" inputMode="email" value={f.email} onChange={set('email')} autoComplete="off" /></label>
        <label className="ta-lfield"><span>Note (optional)</span><textarea rows={2} value={f.notes} onChange={set('notes')} /></label>
        {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
        <div className="ta-sheet-actions">
          <button type="button" className="ta-btn ta-btn--ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="ta-btn ta-btn--primary" disabled={busy}>{busy ? 'Adding…' : 'Add lead'}</button>
        </div>
      </form>
    </div>
  )
}

// ── One lead ────────────────────────────────────────────────────────────────

export function ProgramLead() {
  const entry = useEntry()
  const work = useWorkOn()
  const item = entry.item
  return (
    <Frame entry={entry} tab="leads">
      {item && (work === null ? <Loading /> : work && hasLeads(item) ? <LeadBody item={item} /> : <NotOn entry={entry} />)}
    </Frame>
  )
}

function LeadBody({ item }) {
  const { leadId } = useParams()
  const navigate = useNavigate()
  const req = useMemo(() => programRequest(item), [item])
  const stages = useStages(item)
  const [lead, setLead] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  const [toast, setToast] = useState(null)
  const [logCall, setLogCall] = useState(false)
  const reload = useCallback(() => setN(x => x + 1), [])
  useEffect(() => {
    let live = true
    setError(null)
    api.get('/leads/' + encodeURIComponent(leadId), req)
      .then(d => { if (live) setLead(d) })
      .catch(e => { if (live) setError(e) })
    return () => { live = false }
  }, [leadId, req, n])
  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), 2600); return () => clearTimeout(t) }, [toast])

  if (error) return <>
    <button type="button" className="ta-linkbtn" onClick={() => navigate(mobileProgramPath(item, 'leads'))}>‹ All leads</button>
    <ErrorNote error={error} onRetry={reload} />
  </>
  if (!lead) return <Loading />
  const links = contactLinks(lead)
  async function moveStage(key) {
    try {
      await api.patch('/leads/' + encodeURIComponent(lead.id) + '/stage?tier=' + encodeURIComponent(key), {}, req)
      setLead(l => ({ ...l, tier: key })); setToast('Moved to ' + stageLabel(stages, key))
    } catch (e) { setToast(errText(e, 'Could not move the lead.')) }
  }
  return (
    <div className="ta-lead">
      <button type="button" className="ta-linkbtn ta-lead-back" onClick={() => navigate(mobileProgramPath(item, 'leads'))}>‹ All leads</button>
      <section className="ta-card ta-lead-head">
        <h2 className="ta-lead-name">{leadName(lead)}</h2>
        <p className="ta-row-sub">{[formatPhone(lead.phone), lead.email].filter(Boolean).join(' · ') || 'No phone or email on file'}</p>
        {stages.length > 0 && (
          <label className="ta-lead-stage">
            <span>Stage</span>
            <select value={lead.tier || ''} onChange={e => moveStage(e.target.value)} aria-label="Stage">
              {!stages.some(s => s.key === lead.tier) && <option value={lead.tier || ''}>{stageLabel(stages, lead.tier) || '—'}</option>}
              {stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        )}
      </section>

      <div className="ta-contact" role="group" aria-label="Contact">
        <ContactBtn href={links.call} icon="phone" label="Call" onTap={() => setLogCall(true)} />
        <ContactBtn href={links.text} icon="sms" label="Text" />
        <ContactBtn href={links.email} icon="mail" label="Email" />
      </div>
      <p className="ta-muted ta-small ta-contact-note">Call and Text use your phone. To text from the business number, use “Text from EvoSys” below.</p>

      <FollowUps item={item} lead={lead} req={req} onToast={setToast} />
      <Notes item={item} lead={lead} req={req} onToast={setToast} logCall={logCall} onLogged={() => setLogCall(false)} />
      <EvoText item={item} lead={lead} req={req} onToast={setToast} />

      {toast && <div className="ta-toast" role="status">{toast}</div>}
    </div>
  )
}

function ContactBtn({ href, icon, label, onTap }) {
  if (!href) return <span className="ta-cbtn is-off" aria-disabled="true"><Icon name={icon} size={24} /><span>{label}</span></span>
  return <a className="ta-cbtn" href={href} onClick={onTap}><Icon name={icon} size={24} /><span>{label}</span></a>
}

function FollowUps({ lead, req, onToast }) {
  const [items, setItems] = useState(null)
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('Follow up')
  const [pick, setPick] = useState('tomorrow')
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState(false)
  const [n, setN] = useState(0)
  const choices = useMemo(() => followUpChoices(), [adding]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let live = true
    api.get('/work/tasks?status=open&page_size=50&lead_id=' + encodeURIComponent(lead.id), req)
      .then(d => { if (live) setItems((d && d.items) || []) })
      .catch(() => { if (live) setItems([]) })
    return () => { live = false }
  }, [lead.id, req, n])
  async function add(e) {
    e.preventDefault()
    const when = pick === 'custom' ? (custom ? new Date(custom) : null) : (choices.find(c => c.key === pick) || {}).when
    if (!when || Number.isNaN(when.getTime())) { onToast('Pick when to follow up.'); return }
    setBusy(true)
    try {
      await api.post('/work/tasks', { title: title.trim() || 'Follow up', lead_id: lead.id, due_at: when.toISOString(), source: 'manual' }, req)
      setAdding(false); setTitle('Follow up'); setPick('tomorrow'); setCustom(''); setN(x => x + 1); onToast('Follow-up saved')
    } catch (e2) { onToast(errText(e2, 'Could not save the follow-up.')) } finally { setBusy(false) }
  }
  async function done(t) {
    try { await api.patch('/work/tasks/' + encodeURIComponent(t.id), { status: 'done' }, req); setN(x => x + 1); onToast('Marked done') }
    catch (e) { onToast(errText(e, 'Could not update it.')) }
  }
  return (
    <section className="ta-section" aria-labelledby="ta-fu-h">
      <div className="ta-section-head">
        <h2 id="ta-fu-h" className="ta-h2">Follow-ups</h2>
        {!adding && <button type="button" className="ta-linkbtn" onClick={() => setAdding(true)}>+ Add</button>}
      </div>
      {adding && (
        <form className="ta-card ta-fu-form" onSubmit={add}>
          <label className="ta-lfield"><span>What</span><input value={title} onChange={e => setTitle(e.target.value)} maxLength={300} /></label>
          <div className="ta-chips" role="group" aria-label="When">
            {choices.map(c => <button key={c.key} type="button" className={'ta-chip' + (pick === c.key ? ' is-on' : '')} aria-pressed={pick === c.key} onClick={() => setPick(c.key)}>{c.label}</button>)}
            <button type="button" className={'ta-chip' + (pick === 'custom' ? ' is-on' : '')} aria-pressed={pick === 'custom'} onClick={() => setPick('custom')}>Pick a time</button>
          </div>
          {pick === 'custom' && <label className="ta-lfield"><span>Date and time</span><input type="datetime-local" value={custom} onChange={e => setCustom(e.target.value)} /></label>}
          <div className="ta-sheet-actions">
            <button type="button" className="ta-btn ta-btn--ghost" onClick={() => setAdding(false)}>Cancel</button>
            <button type="submit" className="ta-btn ta-btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
          </div>
        </form>
      )}
      {items === null ? <Loading /> : items.length === 0
        ? (!adding && <div className="ta-card ta-muted">No open follow-ups.</div>)
        : <ul className="ta-cards" aria-label="Open follow-ups">
            {items.map(t => (
              <li key={t.id} className={'ta-card ta-furow' + (t.overdue ? ' is-overdue' : '')}>
                <span className="ta-furow-main">
                  <span className="ta-row-name">{t.title}</span>
                  <span className="ta-row-sub">{dueLabel(t.due_at)}{t.assigned_to_name ? ' · ' + t.assigned_to_name : ''}</span>
                </span>
                <button type="button" className="ta-btn ta-btn--ghost ta-btn--sm" onClick={() => done(t)}><Icon name="check" size={18} /> Done</button>
              </li>
            ))}
          </ul>}
    </section>
  )
}

function Notes({ lead, req, onToast, logCall, onLogged }) {
  const [items, setItems] = useState(null)
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [n, setN] = useState(0)
  const box = useRef(null)
  useEffect(() => {
    let live = true
    api.get('/work/leads/' + encodeURIComponent(lead.id) + '/notes', req)
      .then(d => { if (live) setItems(Array.isArray(d) ? d : (d && d.items) || []) })
      .catch(() => { if (live) setItems([]) })
    return () => { live = false }
  }, [lead.id, req, n])
  // Back from a phone call: offer to write down how it went.
  useEffect(() => {
    if (!logCall) return
    setBody(b => b || 'Called – ')
    if (box.current) { box.current.focus(); box.current.scrollIntoView({ block: 'center' }) }
  }, [logCall])
  async function add(e) {
    e.preventDefault()
    const text = body.trim()
    if (!text || text === 'Called –') return
    setBusy(true)
    try {
      await api.post('/work/leads/' + encodeURIComponent(lead.id) + '/notes', { body: text, kind: 'note' }, req)
      setBody(''); setN(x => x + 1); onLogged(); onToast('Note saved')
    } catch (e2) { onToast(errText(e2, 'Could not save the note.')) } finally { setBusy(false) }
  }
  const sorted = (items || []).slice().sort((a, b) => (parseTs(b.created_at) || 0) - (parseTs(a.created_at) || 0))
  return (
    <section className="ta-section" aria-labelledby="ta-notes-h">
      <h2 id="ta-notes-h" className="ta-h2">Notes</h2>
      <form className="ta-card ta-note-form" onSubmit={add}>
        <label className="ta-sr" htmlFor="ta-note-in">Add a note</label>
        <textarea id="ta-note-in" ref={box} rows={logCall ? 3 : 2} placeholder={logCall ? 'How did the call go?' : 'Add a note…'}
                  value={body} onChange={e => setBody(e.target.value)} maxLength={10000} />
        <button type="submit" className="ta-btn ta-btn--primary ta-btn--sm" disabled={busy || !body.trim()}>{busy ? 'Saving…' : 'Save note'}</button>
      </form>
      {items === null ? <Loading /> : sorted.length === 0
        ? <div className="ta-card ta-muted">No notes yet.</div>
        : <ul className="ta-cards" aria-label="Notes">
            {sorted.map(x => (
              <li key={x.id} className="ta-card ta-noterow">
                <p>{x.body}</p>
                <span className="ta-row-sub">{[x.author_name, ago(x.created_at) && ago(x.created_at) + ' ago'].filter(Boolean).join(' · ')}</span>
              </li>
            ))}
          </ul>}
      {lead.notes && <details className="ta-card ta-oldnotes"><summary>Notes on the lead record</summary><p>{lead.notes}</p></details>}
    </section>
  )
}

/** A text from the business number, through the website's own compose gate and send path. */
function EvoText({ item, lead, req, onToast }) {
  const navigate = useNavigate()
  const [gate, setGate] = useState(null)
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const key = useRef(draftKey())
  // Program workspaces (SCI, Restland) already answer texts in their Replies thread.
  const viaReplies = mobileKind(item) === 'program'
  useEffect(() => {
    if (viaReplies) return undefined
    let live = true
    api.get('/communications/compose-gate/' + encodeURIComponent(lead.id), req)
      .then(g => { if (live) setGate(g) }).catch(() => { if (live) setGate({ allowed: false, reasons: [{ label: 'Texting could not be checked right now.' }] }) })
    return () => { live = false }
  }, [lead.id, req, viaReplies])
  if (viaReplies) {
    return (
      <section className="ta-section">
        <button type="button" className="ta-btn ta-btn--ghost ta-btn--block" onClick={() => navigate(mobileProgramPath(item, 'replies') + '/' + encodeURIComponent(lead.id))}>
          <Icon name="chat" size={20} /> Messages with {leadName(lead).split(' ')[0]}</button>
      </section>
    )
  }
  async function send(e) {
    e.preventDefault()
    const text = body.trim()
    if (!text) return
    setBusy(true)
    try {
      await api.post('/communications/send', { lead_id: lead.id, body: text }, { ...req, headers: { 'Idempotency-Key': key.current } })
      setBody(''); key.current = draftKey(); onToast('Text sent')
    } catch (e2) { onToast(errText(e2, 'The text was not sent.')) } finally { setBusy(false) }
  }
  return (
    <section className="ta-section" aria-labelledby="ta-evo-h">
      <h2 id="ta-evo-h" className="ta-h2">Text from EvoSys</h2>
      {gate === null ? <Loading /> : gate.allowed
        ? <form className="ta-card ta-note-form" onSubmit={send}>
            <textarea rows={2} placeholder="Type a text…" value={body} onChange={e => setBody(e.target.value)} maxLength={1600} aria-label="Text message" />
            <button type="submit" className="ta-btn ta-btn--primary ta-btn--sm" disabled={busy || !body.trim()}><Icon name="send" size={18} /> {busy ? 'Sending…' : 'Send'}</button>
          </form>
        : <div className="ta-card ta-muted">Can’t text from EvoSys: {((gate.reasons || [])[0] || {}).label || 'not allowed for this lead.'}</div>}
    </section>
  )
}
