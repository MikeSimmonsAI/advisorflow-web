/**
 * Lead Details — "Everything you need in one simple view." (Lane F, #281)
 *
 * The action panel a rep works from: who they are, how to reach them, where
 * the deal stands, the next step and its date, and what happened recently.
 * Everything else a deal carries — discovery, demo build, proposals, billing,
 * pricing, reassignment — stays on the full deal record
 * (/sales/opportunities/:id), one click away. Nothing was removed from it.
 *
 * Writes use the existing endpoints only:
 *   PATCH /sales/opportunities/{id}        stage, next action, due date, contact
 *   POST  /sales/opportunities/{id}/notes  notes, call results, texts
 *
 * WON HANDS OFF; IT DOES NOT ONBOARD. When a deal is Won this screen shows the
 * existing onboarding status (GET …/implementation) and links to the existing
 * Sold / Onboarding area. Creating the customer stays a deliberate step in the
 * existing provisioning flow — nothing here creates one.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell, { useSales } from './SalesShell'
import { wallDateTime } from './parts'
import {
  SIMPLE_COLUMNS, STAGE_LABELS, LOST_STAGE, dueBucket, dueIsoFromYmd, telHref,
} from './simpleSales'
import {
  Icon, DueStatus, ErrorLine, Skeleton, initialsOf, useNotice, useContactActions,
  CallOutcomeModal,
} from './SimpleParts'

const TABS = [
  ['overview', 'Overview'], ['activity', 'Activity'], ['notes', 'Notes'], ['next', 'Next Steps'],
]

function when(iso) {
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(String(iso)) ? iso : iso + 'Z')
  return isNaN(d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** "2026-10-23T12:00:00" → "2026-10-23" for a date input, in calendar terms. */
function dueYmd(iso) {
  if (!iso) return ''
  return String(iso).slice(0, 10)
}

export default function LeadDetails() {
  return (
    <SalesShell title="Lead Details" subtitle="Everything you need in one simple view.">
      <LeadDetailsBody />
    </SalesShell>
  )
}

function LeadDetailsBody() {
  const { oppId } = useParams()
  const nav = useNavigate()
  const ctx = useSales()
  const [opp, setOpp] = useState(null)
  const [post, setPost] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('overview')
  const [logging, setLogging] = useState(false)
  const [notice, notify] = useNotice()

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const o = await api.get('/sales/opportunities/' + oppId)
      setOpp(o)
      api.get('/sales/opportunities/' + oppId + '/implementation').then(setPost).catch(() => setPost(null))
    } catch (e) {
      setError(e.message || 'Could not load this lead.')
    } finally {
      setLoading(false)
    }
  }, [oppId])
  useEffect(() => { load() }, [load])

  const contact = useContactActions({
    me: ctx?.user?.full_name, notify,
    onChanged: () => load(),
    onBook: o => nav('/sales/book?opp=' + encodeURIComponent(o.id)),
  })

  if (loading && !opp) return <div className="ss-panel"><Skeleton rows={6} /></div>
  if (!opp) return <><BackLink /><ErrorLine error={error || 'Lead not found.'} onRetry={load} /></>

  const name = opp.contact_name || opp.company_name
  const bucket = opp.status === 'open' ? dueBucket(opp.next_action_due_at) : null
  const isWon = ['won', 'onboarding', 'live'].includes(opp.stage)
  const hasPhone = !!telHref(opp.phone)

  return (
    <>
      <BackLink />
      <ErrorLine error={error} onRetry={load} />
      <section className="ss-panel">
        <div className="ss-panelbody" style={{ paddingBottom: 7 }}>
          <div className="ss-detailbar">
            <div className="ss-avatar" aria-hidden="true">{initialsOf(name)}</div>
            <div style={{ minWidth: 0 }}>
              <h2>{name}</h2>
              <p>{opp.contact_name ? opp.company_name : (opp.industry || '')}</p>
              {isWon ? <span className="ss-status ss-sgreen">Won</span>
                : opp.stage === LOST_STAGE ? <span className="ss-status">Lost</span>
                : <DueStatus bucket={bucket} iso={opp.next_action_due_at} />}
            </div>
            <div className="ss-detail-actions">
              <button type="button" className="ss-btn ss-green" disabled={!hasPhone}
                      title={hasPhone ? '' : 'No phone number on this record'}
                      onClick={() => contact.call(opp)}>
                <Icon name="phone" /> Call
              </button>
              <button type="button" className="ss-btn ss-blue" disabled={!hasPhone}
                      title={hasPhone ? '' : 'No phone number on this record'}
                      onClick={() => contact.text(opp)}>
                <Icon name="message" /> Text
              </button>
              <button type="button" className="ss-btn ss-soft"
                      onClick={() => nav('/sales/book?opp=' + encodeURIComponent(opp.id))}>
                <Icon name="calendar" /> Book Demo
              </button>
            </div>
          </div>
        </div>

        <div className="ss-tabs" role="tablist" aria-label="Lead sections">
          {TABS.map(([k, label]) => (
            <button key={k} type="button" role="tab" id={'ss-tab-' + k} aria-selected={tab === k}
                    aria-controls={'ss-pane-' + k} className="ss-tab" onClick={() => setTab(k)}>
              {label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={'ss-pane-' + tab} aria-labelledby={'ss-tab-' + tab}>
          {tab === 'overview' && (
            <>
              <div className="ss-panelbody">
                <div className="ss-twobox">
                  <ContactInfo opp={opp} onAddNote={() => setTab('notes')} />
                  <div>
                    <h3>Status &amp; Next Action</h3>
                    <StageField opp={opp} onSaved={o => { setOpp(prev => ({ ...prev, ...o })); notify('Stage updated.'); load() }} />
                    <NextActionFields opp={opp} onSaved={() => { notify('Next action saved.'); load() }} compact />
                    <button type="button" className="ss-btn ss-green" onClick={() => setLogging(true)}>
                      <Icon name="check" /> Log Call Outcome
                    </button>
                  </div>
                </div>
              </div>
              {(isWon || post?.provisioned) && <div className="ss-panelbody ss-divide"><WonHandoff opp={opp} post={post} isGod={!!ctx?.is_god} /></div>}
              <div className="ss-panelbody ss-divide">
                <h3 style={{ fontSize: 15, margin: '0 0 6px' }}>Recent Activity</h3>
                <Timeline items={(opp.timeline || []).slice(0, 4)} />
                {(opp.timeline || []).length > 4 && (
                  <button type="button" className="ss-link" onClick={() => setTab('activity')}>
                    See all activity <Icon name="arrow" size={14} />
                  </button>
                )}
              </div>
            </>
          )}

          {tab === 'activity' && (
            <div className="ss-panelbody">
              <Timeline items={opp.timeline || []} />
            </div>
          )}

          {tab === 'notes' && (
            <div className="ss-panelbody">
              <NoteForm oppId={opp.id} onSaved={() => { notify('Note added.'); load() }} />
              <Timeline items={(opp.timeline || []).filter(e => e.event_type === 'note')}
                        empty="No notes yet." />
            </div>
          )}

          {tab === 'next' && (
            <div className="ss-panelbody">
              <div className="ss-twobox">
                <div>
                  <h3>Next step</h3>
                  <NextActionFields opp={opp} onSaved={() => { notify('Next action saved.'); load() }} />
                </div>
                <div>
                  <h3>Upcoming meetings</h3>
                  {(opp.appointments || []).length === 0
                    ? <p className="ss-count-note" style={{ margin: '0 0 12px' }}>Nothing booked.</p>
                    : (opp.appointments || []).map(a => (
                      <div key={a.id} className="ss-compact">
                        <strong>{wallDateTime(a.starts_at_local || a.starts_at)}</strong>
                        <span className="ss-sp">{a.title || a.meeting_type}</span>
                        <span className={'ss-status ' + (a.confirmation_status === 'confirmed' ? 'ss-sgreen' : 'ss-amber')}>
                          {a.confirmation_status === 'confirmed' ? 'Confirmed' : 'Not confirmed'}
                        </span>
                      </div>
                    ))}
                  <button type="button" className="ss-btn ss-white ss-xs" style={{ marginTop: 10 }}
                          onClick={() => nav('/sales/book?opp=' + encodeURIComponent(opp.id))}>
                    <Icon name="calendar" /> Book Demo
                  </button>
                </div>
              </div>
              {(isWon || post?.provisioned) && <div style={{ marginTop: 18 }}><WonHandoff opp={opp} post={post} isGod={!!ctx?.is_god} /></div>}
              <p className="ss-footnote">
                Discovery, demo build, proposals and billing live on the{' '}
                <button type="button" className="ss-link" onClick={() => nav('/sales/opportunities/' + opp.id)}>
                  full deal record <Icon name="arrow" size={14} />
                </button>
              </p>
            </div>
          )}
        </div>
      </section>

      {logging && (
        <CallOutcomeModal
          opp={opp}
          onClose={() => setLogging(false)}
          onBook={o => nav('/sales/book?opp=' + encodeURIComponent(o.id))}
          onSaved={() => { setLogging(false); notify('Call result saved.'); load() }}
        />
      )}
      {contact.modals}
      {notice}
    </>
  )
}

function BackLink() {
  const nav = useNavigate()
  return (
    <button type="button" className="ss-back" onClick={() => nav('/sales/follow-ups')}>
      <Icon name="chevleft" /> Back to Follow Ups
    </button>
  )
}

function ContactInfo({ opp, onAddNote }) {
  const tel = telHref(opp.phone)
  const site = opp.website ? (/^https?:\/\//i.test(opp.website) ? opp.website : 'https://' + opp.website) : null
  return (
    <div>
      <h3>Contact Info</h3>
      <div className="ss-info-item"><Icon name="phone" />
        {opp.phone ? (tel ? <a href={tel}>{opp.phone}</a> : opp.phone) : <span>No phone number</span>}</div>
      <div className="ss-info-item"><Icon name="mail" />
        {opp.email ? <a href={'mailto:' + opp.email}>{opp.email}</a> : <span>No email</span>}</div>
      {site && <div className="ss-info-item"><Icon name="globe" />
        <a href={site} target="_blank" rel="noreferrer">{opp.website}</a></div>}
      {opp.timezone && <div className="ss-info-item"><Icon name="pin" /><span>{opp.timezone}</span></div>}
      {opp.owner_name && <div className="ss-info-item"><Icon name="users" /><span>Owner: {opp.owner_name}</span></div>}
      <button type="button" className="ss-link" onClick={onAddNote}>Add a note <Icon name="arrow" size={14} /></button>
    </div>
  )
}

/**
 * Pipeline stage. The four simple columns are the groups; the real stages are
 * the choices, so the deal always lands on an exact lifecycle stage and
 * nothing is remapped. Won and Lost ask for one confirming click.
 */
export function StageField({ opp, onSaved, small }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(null)

  async function apply(stage) {
    setBusy(true); setError(null)
    try {
      const o = await api.patch('/sales/opportunities/' + opp.id, { stage })
      setPending(null)
      onSaved && onSaved(o || { stage })
    } catch (e) {
      setError(e.message || 'Could not change the stage.')
    } finally {
      setBusy(false)
    }
  }

  function choose(stage) {
    if (stage === opp.stage) return
    if (stage === 'won' || stage === LOST_STAGE) setPending(stage)
    else apply(stage)
  }

  const id = 'ss-stage-' + opp.id
  return (
    <div className={small ? '' : 'ss-field'}>
      {!small && <label htmlFor={id}>Pipeline stage</label>}
      <select id={id} value={opp.stage} disabled={busy} aria-label={small ? 'Stage for ' + (opp.contact_name || opp.company_name) : undefined}
              onChange={e => choose(e.target.value)}>
        {SIMPLE_COLUMNS.map(c => (
          <optgroup key={c.key} label={c.label}>
            {c.stages.map(s => <option key={s} value={s}>{c.label === STAGE_LABELS[s] ? c.label : c.label + ' — ' + STAGE_LABELS[s]}</option>)}
          </optgroup>
        ))}
        <optgroup label="Closed">
          <option value={LOST_STAGE}>Lost</option>
        </optgroup>
        {![...SIMPLE_COLUMNS.flatMap(c => c.stages), LOST_STAGE].includes(opp.stage) && (
          <option value={opp.stage}>{opp.stage_label || opp.stage}</option>
        )}
      </select>
      {pending && (
        <div className="ss-okbar" style={{ marginTop: 8, marginBottom: 0 }} role="alertdialog" aria-label="Confirm stage">
          <span>{pending === 'won'
            ? 'Mark this deal Won? It then hands off to onboarding.'
            : 'Mark this deal Lost? It leaves your open pipeline.'}</span>
          <span style={{ flex: 1 }} />
          <button type="button" className="ss-btn ss-white ss-xs" onClick={() => setPending(null)}>Cancel</button>
          <button type="button" className={'ss-btn ss-xs ' + (pending === 'won' ? 'ss-green' : 'ss-soft')}
                  disabled={busy} onClick={() => apply(pending)}>
            {pending === 'won' ? 'Yes, mark Won' : 'Yes, mark Lost'}
          </button>
        </div>
      )}
      {error && <div className="ss-err" style={{ marginTop: 8 }} role="alert">{error}</div>}
    </div>
  )
}

function NextActionFields({ opp, onSaved, compact }) {
  const [action, setAction] = useState(opp.next_action || '')
  const [due, setDue] = useState(dueYmd(opp.next_action_due_at))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => { setAction(opp.next_action || ''); setDue(dueYmd(opp.next_action_due_at)) },
    [opp.next_action, opp.next_action_due_at])
  const dirty = action !== (opp.next_action || '') || due !== dueYmd(opp.next_action_due_at)

  async function save() {
    setBusy(true); setError(null)
    try {
      const body = { next_action: action.trim() || null }
      const iso = dueIsoFromYmd(due)
      if (iso) body.next_action_due_at = iso
      await api.patch('/sales/opportunities/' + opp.id, body)
      onSaved && onSaved()
    } catch (e) {
      setError(e.message || 'Could not save the next action.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="ss-field" style={compact ? { marginBottom: 10 } : undefined}>
        <label htmlFor={'ss-na-' + opp.id + (compact ? 'c' : '')}>Next action</label>
        <input id={'ss-na-' + opp.id + (compact ? 'c' : '')} value={action} maxLength={200}
               placeholder="What happens next?" onChange={e => setAction(e.target.value)} />
      </div>
      <div className="ss-field">
        <label htmlFor={'ss-nd-' + opp.id + (compact ? 'c' : '')}>Due</label>
        <input id={'ss-nd-' + opp.id + (compact ? 'c' : '')} type="date" value={due}
               onChange={e => setDue(e.target.value)} />
      </div>
      {error && <div className="ss-err" role="alert">{error}</div>}
      {dirty && (
        <button type="button" className="ss-btn ss-blue" disabled={busy} onClick={save}
                style={{ marginRight: 8, marginBottom: 10 }}>
          {busy ? 'Saving…' : 'Save next action'}
        </button>
      )}
    </>
  )
}

function NoteForm({ oppId, onSaved }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  async function save() {
    const t = text.trim()
    if (!t) return
    setBusy(true); setError(null)
    try {
      await api.post('/sales/opportunities/' + oppId + '/notes', {
        event_type: 'note', summary: t.slice(0, 250), detail: t.length > 250 ? t : null,
      })
      setText('')
      onSaved && onSaved()
    } catch (e) {
      setError(e.message || 'Could not save the note.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="ss-field">
        <label htmlFor="ss-note-new">Add a note</label>
        <textarea id="ss-note-new" value={text} maxLength={4000}
                  placeholder="Anything worth remembering about this lead…"
                  onChange={e => setText(e.target.value)} />
      </div>
      {error && <div className="ss-err" role="alert">{error}</div>}
      <button type="button" className="ss-btn ss-blue" disabled={busy || !text.trim()} onClick={save}>
        {busy ? 'Saving…' : 'Save note'}
      </button>
    </div>
  )
}

const EVENT_TITLES = {
  note: 'Note', call_outcome: 'Call', text_logged: 'Text', stage_changed: 'Stage changed',
  created: 'Lead added', appointment_booked: 'Meeting booked', reassigned: 'Reassigned',
}

function Timeline({ items, empty }) {
  if (!items.length) return <p className="ss-count-note">{empty || 'No activity yet.'}</p>
  return items.map(e => (
    <div key={e.id} className="ss-activityline">
      <span className="ss-dot" aria-hidden="true" />
      <div style={{ minWidth: 0 }}>
        <strong>{EVENT_TITLES[e.event_type] || String(e.event_type || '').replace(/_/g, ' ')}</strong>
        <p>{when(e.occurred_at)}{e.actor_name ? ' · ' + e.actor_name : ''} · {e.summary}</p>
        {e.detail && <p>{e.detail}</p>}
      </div>
    </div>
  ))
}

/**
 * After the sale. Reads the EXISTING onboarding status and points at the
 * EXISTING onboarding area. Starting onboarding (creating the customer) is the
 * deliberate provisioning step that already exists — offered here only to the
 * account that can open that screen today.
 */
function WonHandoff({ opp, post, isGod }) {
  const nav = useNavigate()
  const live = post?.provisioned
  return (
    <div className="ss-callout ss-wonbox">
      <strong>{live ? 'Onboarding is under way.' : 'Won — ready to hand off to onboarding.'}</strong><br />
      {live
        ? <>Customer: {post.customer_organization_name || '—'} · {post.status_label || ''}
            {post.percent_complete != null ? ' · ' + post.percent_complete + '% complete' : ''}</>
        : 'Onboarding is set up in its own area. Your manager starts it from the won deal; you can follow it there.'}
      <div className="ss-actrow" style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
        <button type="button" className="ss-btn ss-green ss-xs" onClick={() => nav('/sales/onboarding')}>
          Open Sold / Onboarding <Icon name="arrow" size={14} />
        </button>
        {!live && isGod && (
          <button type="button" className="ss-btn ss-white ss-xs" onClick={() => nav('/god/provision/' + opp.id)}>
            Start onboarding (owner)
          </button>
        )}
        <button type="button" className="ss-btn ss-white ss-xs" onClick={() => nav('/sales/opportunities/' + opp.id)}>
          Full deal record
        </button>
      </div>
    </div>
  )
}
