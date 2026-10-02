/**
 * PROSPECT DETAIL — one family's record, end to end.
 *
 *   GET   /agency/prospects/{id}                 profile, provenance, history, conversation, cases
 *   GET   /agency/prospects/{id}/brief           FACT / INFERENCE / INSUFFICIENT INFORMATION
 *   GET   /agency/prospects/{id}/recommendation  explainable agent recommendation
 *   POST  /agency/prospects/{id}/assign          offer to an agent (managers)
 *   POST  /agency/assignments/{aid}/accept|decline
 *   GET   /agency/prospects/{id}/copilot         conversation copilot
 *   POST  /agency/prospects/{id}/copilot/simulate-send   SIMULATED — no provider is called
 *   POST  /agency/prospects/{id}/automation | /takeover | /tasks
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { readAuthority } from '../../auth/workspaceAuthority'
import { factText, fmtDate, fmtDateTime, generatedByLabel, humanize, relTime } from './agencyFormat'
import { NewApplicationForm, NewAppointmentForm, ProfileEditor, StatedNote } from './AgencyForms'
import ConversationBrain from '../../components/ConversationBrain'
import {
  AgencyPage, DemoBadge, Empty, ErrorState, Loading, Notice, Pill, Section, useAction, useAgency,
} from './agencyUi'

export function GeneratedBy({ out, onAssist, busy }) {
  const g = generatedByLabel(out)
  return (
    <span className="ag-genby" data-testid="generated-by">
      <Pill value={g.label} label={g.label} tone={g.tone} />
      {onAssist && !(out?.generated_by === 'ai') ? (
        <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={onAssist}
          title="Ask AI to rephrase from these facts only. A verifier rejects any new numbers, names, products or claims.">
          Rephrase with AI (verified)</button>) : null}
      {g.note ? <span className="ag-muted ag-small">{g.note}</span> : null}
    </span>
  )
}

export function BriefView({ brief, compact, onAssist, busy }) {
  return (
    <div className={`ag-brief${compact ? ' ag-brief--compact' : ''}`} data-testid="brief">
      <div className="ag-brief__top">
        {brief.category ? <Pill value={brief.category} tone="gold" /> : null}
        {brief.intent_level ? <Pill value={brief.intent_level} label={`${humanize(brief.intent_level)} intent (recorded)`} /> : null}
        <GeneratedBy out={brief} onAssist={onAssist} busy={busy} />
      </div>
      {brief.narrative && !compact ? <p className="ag-brief__narrative" data-testid="brief-narrative">{brief.narrative}</p> : null}
      {brief.recommended_action ? <div className="ag-brief__action"><span className="ag-k">Recommended next action</span><strong>{brief.recommended_action}</strong></div> : null}
      <div className="ag-brief__cols">
        <div className="ag-brief__col ag-brief__col--fact">
          <h3>FACT <span>from the record</span></h3>
          {brief.facts?.length ? (
            <dl className="ag-facts">
              {brief.facts.map((f, i) => (
                <div key={i}><dt>{f.label}</dt><dd>{factText(f.value)}<code title="Source field">{f.source_field}</code></dd></div>
              ))}
            </dl>
          ) : <p className="ag-muted">No recorded facts.</p>}
        </div>
        <div className="ag-brief__col ag-brief__col--inf">
          <h3>INFERENCE <span>to verify in conversation</span></h3>
          {brief.inferences?.length ? (
            <ul>{brief.inferences.map((x, i) => (
              <li key={i}>{x.statement}<div className="ag-small ag-muted">Basis: {(x.basis || []).join(', ') || '—'} · confidence {x.confidence}</div></li>
            ))}</ul>
          ) : <p className="ag-muted">None drawn.</p>}
        </div>
        <div className="ag-brief__col ag-brief__col--gap">
          <h3>INSUFFICIENT INFORMATION <span>still unknown</span></h3>
          {brief.insufficient?.length ? <ul>{brief.insufficient.map((x, i) => <li key={i}>{x}</li>)}</ul> : <p className="ag-muted">Nothing flagged.</p>}
        </div>
      </div>
      {brief.suggested_agent ? (
        <div className="ag-brief__agent"><span className="ag-k">Suggested agent</span> <strong>{brief.suggested_agent.name}</strong>
          <ul className="ag-reasons">{brief.suggested_agent.reasons.map(r => <li key={r}>{r}</li>)}</ul></div>
      ) : null}
      {brief.disclaimer ? <Notice>{brief.disclaimer}</Notice> : null}
    </div>
  )
}

function Recommendation({ id, isManager, onChanged }) {
  const rec = useAgency(`/agency/prospects/${id}/recommendation`)
  const [run, busy, error] = useAction()
  const [note, setNote] = useState('')
  const assign = async (agentId) => {
    const r = await run(() => api.post(`/agency/prospects/${id}/assign`, { agent_id: agentId, note: note || undefined }))
    if (r) { rec.reload(); onChanged() }
  }
  if (rec.loading && !rec.data) return <Loading label="Scoring eligible agents" />
  if (rec.error) return <ErrorState error={rec.error} onRetry={rec.reload} />
  const d = rec.data
  return (
    <div className="ag-rec" data-testid="recommendation">
      {d.recommended ? (
        <div className="ag-rec__best">
          <span className="ag-k">Recommended</span>
          <strong className="ag-rec__name">{d.recommended.name}</strong>
          <ul className="ag-reasons">{d.recommended.reasons.map(r => <li key={r}>{r}</li>)}</ul>
        </div>
      ) : <p className="ag-muted">No eligible agent with the configured factors.</p>}
      <ul className="ag-cands">{d.candidates.map(c => (
        <li key={c.agent_id} className="ag-cand">
          <span><strong>{c.name}</strong> {c.eligible ? <Pill value="yes" label="Eligible" tone="green" /> : <Pill value="no" label="Blocked" tone="red" />}</span>
          {isManager ? <button className="ag-btn ag-btn--sm" disabled={busy || !c.eligible} onClick={() => assign(c.agent_id)}>Assign</button> : <span />}
          <span className="ag-cand__why">{c.reasons.join(' · ')}{c.blockers.length ? <span className="ag-red"> — {c.blockers.join('; ')}</span> : null}</span>
        </li>))}
      </ul>
      {isManager ? <input className="ag-input" value={note} onChange={e => setNote(e.target.value)} placeholder="Optional note to the agent" aria-label="Assignment note" /> : null}
      {d.unavailable_factors?.length ? (
        <div className="ag-unavail"><span className="ag-k">Not considered — data unavailable</span><ul>{d.unavailable_factors.map(f => <li key={f}>{f}</li>)}</ul></div>
      ) : null}
      {error ? <ErrorState error={error} /> : null}
    </div>
  )
}

function AssignmentHistory({ rows, onChanged }) {
  const [run, busy, error] = useAction()
  const [reason, setReason] = useState('')
  if (!rows?.length) return <p className="ag-muted">Never offered.</p>
  const act = async (a, verb) => {
    const r = await run(() => api.post(`/agency/assignments/${a.id}/${verb}`, verb === 'decline' ? { reason: reason || undefined } : {}))
    if (r !== undefined) onChanged()
  }
  return (
    <>
      <ol className="ag-timeline">
        {rows.map(a => (
          <li key={a.id}>
            <div><Pill value={a.state} /> <strong>{a.agent?.name || 'No agent'}</strong> <span className="ag-muted ag-small">attempt {a.attempt} · offered {fmtDateTime(a.offered_at)}{a.expires_at && a.state === 'offered' ? ` · expires ${relTime(a.expires_at)}` : ''}</span></div>
            {a.decline_reason ? <div className="ag-small">Declined: {a.decline_reason}</div> : null}
            {a.state === 'offered' ? (
              <div className="ag-row">
                <button className="ag-btn ag-btn--sm" disabled={busy} onClick={() => act(a, 'accept')}>Accept</button>
                <input className="ag-input ag-input--sm" value={reason} onChange={e => setReason(e.target.value)} placeholder="Decline reason" aria-label="Decline reason" />
                <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={() => act(a, 'decline')}>Decline</button>
              </div>) : null}
          </li>))}
      </ol>
      {error ? <ErrorState error={error} /> : null}
    </>
  )
}

function Copilot({ id, prospect, onChanged, readOnly = false }) {
  const [assist, setAssist] = useState(false)
  const cp = useAgency(`/agency/prospects/${id}/copilot${assist ? '?assist=ai' : ''}`)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [approved, setApproved] = useState(false)
  const [flash, setFlash] = useState('')
  const [run, busy, error] = useAction()
  useEffect(() => { if (cp.data) { setDraft(cp.data.suggested_reply || ''); setApproved(false); setEditing(false) } }, [cp.data])
  if (cp.loading && !cp.data) return <Loading label="Reading the conversation" />
  if (cp.error) return <ErrorState error={cp.error} onRetry={cp.reload} />
  const c = cp.data
  const done = (msg) => { setFlash(msg); cp.reload(); onChanged() }
  const simulate = async () => { const r = await run(() => api.post(`/agency/prospects/${id}/copilot/simulate-send`, { body: draft })); if (r) done('Recorded as a SIMULATED send. No message was delivered.') }
  const task = async () => {
    const t = c.suggested_task || { title: c.next_best_question ? `Ask: ${c.next_best_question}` : 'Follow up', kind: 'follow_up' }
    const r = await run(() => api.post(`/agency/prospects/${id}/tasks`, { title: t.title, kind: t.kind || 'follow_up' }))
    if (r) done('Task created.')
  }
  const pause = async () => { const r = await run(() => api.post(`/agency/prospects/${id}/automation`, { paused: !c.automation_paused })); if (r) done(r.automation_paused ? 'Automation paused.' : 'Automation resumed.') }
  const takeover = async () => { const r = await run(() => api.post(`/agency/prospects/${id}/takeover`, {})); if (r) done('Human takeover recorded; automation paused.') }
  return (
    <div className="ag-copilot" data-testid="copilot">
      <p className="ag-small">{c.summary}</p>
      {c.detected?.length ? (
        <ul className="ag-detected">{c.detected.map((d, i) => (
          <li key={i}><Pill value={d.type} label={humanize(d.type)} tone={d.type === 'objection' ? 'red' : 'amber'} /> <strong>{d.label}</strong>{d.quote ? <q>{d.quote}</q> : null}</li>))}
        </ul>) : null}
      {c.unanswered?.length ? <div className="ag-small"><span className="ag-k">Unanswered</span> {c.unanswered.join(' · ')}</div> : null}
      {c.recommend_human_takeover ? <div className="ag-alert">Human takeover recommended: {c.takeover_reasons.join('; ')}</div> : null}
      <div className="ag-draft">
        <div className="ag-draft__head"><span className="ag-k">Suggested reply</span><GeneratedBy out={c} busy={cp.loading} onAssist={c.suggested_reply ? () => (assist ? cp.reload() : setAssist(true)) : null} />{c.reply_framing ? <span className="ag-muted ag-small">{c.reply_framing}</span> : null}{approved ? <Pill value="approved" label="Approved" tone="green" /> : null}</div>
        {editing ? <textarea className="ag-input ag-textarea" value={draft} onChange={e => { setDraft(e.target.value); setApproved(false) }} aria-label="Edit reply" rows={5} />
          : <p className="ag-draft__body">{draft || <span className="ag-muted">No reply suggested.</span>}</p>}
      </div>
      {c.next_best_question ? <div className="ag-small"><span className="ag-k">Next best question</span> {c.next_best_question}</div> : null}
      {readOnly ? <p className="ag-muted ag-small">Accept the offer to work this conversation.</p> : (
      <div className="ag-copilot__actions">
        <button className="ag-btn ag-btn--sm" disabled={busy || !draft} onClick={() => { setApproved(true); setEditing(false) }}>Approve</button>
        <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={() => setEditing(e => !e)}>{editing ? 'Done editing' : 'Edit'}</button>
        <button className="ag-btn ag-btn--sm ag-btn--sim" disabled={busy || !draft || !approved} onClick={simulate} title="Records a simulated outbound event only — nothing is sent"
          data-testid="simulate-send">Simulate Send <span>(simulated — nothing is sent)</span></button>
        <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={task}>Create Task</button>
        <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy} onClick={pause}>{c.automation_paused ? 'Resume Automation' : 'Pause Automation'}</button>
        <button className="ag-btn ag-btn--sm ag-btn--ghost" disabled={busy || c.human_takeover} onClick={takeover}>{c.human_takeover ? 'Human in control' : 'Human Takeover'}</button>
      </div>)}
      {!approved && draft ? <p className="ag-muted ag-small">Approve the reply before simulating a send.</p> : null}
      {flash ? <p className="ag-ok" role="status">{flash}</p> : null}
      {error ? <ErrorState error={error} /> : null}
      <Notice>Copilot suggestions are conversation aids, not insurance advice. {prospect.sms_consent ? '' : 'No SMS consent is recorded for this prospect.'}</Notice>
    </div>
  )
}

function Conversation({ rows }) {
  if (!rows?.length) return <p className="ag-muted">No messages yet.</p>
  return (
    <ol className="ag-convo">
      {rows.map(m => (
        <li key={m.id} className={`ag-msg ag-msg--${m.direction}${m.simulated ? ' ag-msg--sim' : ''}`}>
          <div className="ag-msg__body">{m.body}</div>
          <div className="ag-msg__meta">{m.direction === 'inbound' ? 'Prospect' : 'Agency'} · {m.channel} · {fmtDateTime(m.at)}{m.simulated ? ' · SIMULATED (not sent)' : ''}</div>
        </li>))}
    </ol>
  )
}

export default function ProspectDetail() {
  const { prospectId } = useParams()
  const isManager = readAuthority().isManager
  const q = useAgency(`/agency/prospects/${prospectId}`)
  const [briefAssist, setBriefAssist] = useState(false)
  const brief = useAgency(`/agency/prospects/${prospectId}/brief${briefAssist ? '?assist=ai' : ''}`)
  const reloadAll = () => { q.reload(); brief.reload() }
  const [open, setOpen] = useState(null) // 'profile' | 'appointment' | 'application'
  const [flash, setFlash] = useState('')
  const toggle = (k) => { setFlash(''); setOpen(o => (o === k ? null : k)) }
  const created = (msg) => { setOpen(null); setFlash(msg); reloadAll() }
  if (q.loading && !q.data) return <AgencyPage title="Prospect"><Loading /></AgencyPage>
  if (q.error) return <AgencyPage title="Prospect"><ErrorState error={q.error} onRetry={q.reload} /><Link to="/agency/prospects" className="ag-link">← Prospects</Link></AgencyPage>
  const p = q.data
  // Opened through an open offer (not yet accepted): read only until accepted.
  const readOnly = !isManager && p && p.assignment_state === 'offered' && !p.assigned_agent
  const pr = p.profile || {}
  const prov = p.provenance || {}
  return (
    <AgencyPage title={p.name || 'Prospect'} eyebrow={<Link to="/agency/prospects">Prospects & Families</Link>} testid="agency-prospect"
      actions={<><DemoBadge on={p.is_demo} /><Pill value={p.intent_level} label={p.intent_level ? `${humanize(p.intent_level)} intent` : null} /><Pill value={p.assignment_state} /></>}>
      <div className="ag-contact ag-small">
        {[p.email, p.phone, p.state, p.assigned_agent ? `Agent: ${p.assigned_agent.name}` : 'No agent assigned', `Status: ${humanize(p.status)}`].filter(Boolean).map(x => <span key={x}>{x}</span>)}
      </div>
      {flash ? <p className="ag-ok" role="status" data-testid="detail-flash">{flash}</p> : null}
      <div className="ag-grid ag-grid--detail">
        <div className="ag-col">
          <Section title="AI Opportunity Brief">
            {brief.loading && !brief.data ? <Loading label="Building brief" /> : brief.error ? <ErrorState error={brief.error} onRetry={brief.reload} /> : brief.data ? <BriefView brief={brief.data} busy={brief.loading} onAssist={() => (briefAssist ? brief.reload() : setBriefAssist(true))} /> : null}
          </Section>
          <Section title="Conversation & Copilot" className="ag-anchor" aside={<span id="copilot" />}>
            <Conversation rows={p.conversation} />
            <Copilot id={prospectId} prospect={p} onChanged={q.reload} readOnly={readOnly} />
          </Section>
          {readOnly ? null : <ConversationBrain leadId={prospectId} compact />}
          <Section title="Applications" aside={readOnly ? null : <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" onClick={() => toggle('application')} data-testid="open-new-application">{open === 'application' ? 'Close' : '+ Start application'}</button>}>
            {open === 'application' ? <NewApplicationForm prospect={{ id: p.id, name: p.name, agentId: p.assigned_agent?.id }} onCancel={() => setOpen(null)} onCreated={() => created('Application started as a draft.')} /> : null}
            {p.applications?.length ? <ul className="ag-linklist">{p.applications.map(a => <li key={a.id}><Link to={`/agency/applications/${a.id}`}>{humanize(a.product_category) || 'Application'} · {a.carrier || 'carrier not recorded'}</Link> <Pill value={a.status} />{a.stalled ? <Pill value="stalled" label="Stalled" /> : null}</li>)}</ul> : <p className="ag-muted">No applications.</p>}
          </Section>
          <Section title="Policies">
            {p.policies?.length ? <ul className="ag-linklist">{p.policies.map(x => <li key={x.id}><Link to={`/agency/policies/${x.id}`}>{x.policy_number || 'Policy'} · {humanize(x.product_category)}</Link> <Pill value={x.status} /></li>)}</ul> : <p className="ag-muted">No policies.</p>}
          </Section>
        </div>
        <div className="ag-col">
          <Section title="Assignment">
            {p.assigned_agent && p.assignment_state === 'accepted' ? <p>Accepted by <strong>{p.assigned_agent.name}</strong>.</p> : isManager ? <Recommendation id={prospectId} isManager={isManager} onChanged={reloadAll} />
              : <p className="ag-muted">Your manager assigns prospects. An offer to you appears below — accept or decline it there.</p>}
            <h3 className="ag-h3">History</h3>
            <AssignmentHistory rows={p.assignment_history} onChanged={reloadAll} />
          </Section>
          <Section title="Family profile" aside={readOnly ? null : <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" onClick={() => toggle('profile')} data-testid="edit-profile">{open === 'profile' ? 'Close' : 'Edit stated facts'}</button>}>
            {open === 'profile' ? <ProfileEditor prospectId={p.id} profile={pr} onCancel={() => setOpen(null)} onSaved={() => created('Profile updated with the prospect\'s stated facts.')} /> : <>
            <p className="ag-muted ag-small">As stated by the prospect — not verified.</p>
            <dl className="ag-facts ag-facts--plain">
              <div><dt>Household</dt><dd>{factText(pr.household)}</dd></div>
              <div><dt>Preferred contact</dt><dd>{pr.preferred_contact || 'Not stated'}</dd></div>
              <div><dt>Need categories</dt><dd>{factText(pr.need_categories)}</dd></div>
              <div><dt>Financial goals</dt><dd>{factText(pr.financial_goals)}</dd></div>
              <div><dt>Stated concerns</dt><dd>{factText(pr.stated_concerns)}</dd></div>
              <div><dt>Retirement interest</dt><dd>{pr.retirement_interest == null ? 'Not stated' : factText(pr.retirement_interest)}</dd></div>
              <div><dt>Business-owner interest</dt><dd>{pr.business_owner_interest == null ? 'Not stated' : factText(pr.business_owner_interest)}</dd></div>
              <div><dt>Living-benefits interest</dt><dd>{pr.living_benefits_interest == null ? 'Not stated' : factText(pr.living_benefits_interest)}</dd></div>
            </dl>
            <StatedNote /></>}
          </Section>
          <Section title="Provenance & consent">
            <dl className="ag-facts ag-facts--plain">
              <div><dt>Source</dt><dd>{prov.source || '—'}{prov.source_detail ? ` · ${prov.source_detail}` : ''}</dd></div>
              <div><dt>Page</dt><dd>{prov.page || '—'}</dd></div>
              <div><dt>UTM</dt><dd>{factText(prov.utm)}</dd></div>
              <div><dt>Submitted</dt><dd>{fmtDateTime(prov.submitted_at)}</dd></div>
            </dl>
            {prov.consent_events?.length ? (
              <ul className="ag-linklist">{prov.consent_events.map((c, i) => <li key={i}><Pill value={c.granted ? 'granted' : 'declined'} label={`${c.type.toUpperCase()} ${c.granted ? 'granted' : 'not granted'}`} tone={c.granted ? 'green' : 'muted'} /> {fmtDateTime(c.at)} {c.source ? `· ${c.source}` : ''}{c.text ? <div className="ag-small ag-muted">“{c.text}”</div> : null}</li>)}</ul>
            ) : <p className="ag-muted ag-small">No consent events recorded. Consent is never inferred.</p>}
          </Section>
          <Section title="Appointments" aside={readOnly ? null : <button type="button" className="ag-btn ag-btn--sm ag-btn--ghost" onClick={() => toggle('appointment')} data-testid="open-new-appointment">{open === 'appointment' ? 'Close' : '+ New appointment'}</button>}>
            {open === 'appointment' ? <NewAppointmentForm prospect={{ id: p.id, name: p.name, agentId: p.assigned_agent?.id }} onCancel={() => setOpen(null)} onCreated={() => created('Appointment recorded as pending.')} /> : null}
            {p.appointments?.length ? <ul className="ag-linklist">{p.appointments.map(a => <li key={a.id}><Link to={`/agency/appointments/${a.id}`}>{humanize(a.type)} · {humanize(a.medium)} · {fmtDateTime(a.starts_at)}</Link> <Pill value={a.status} /></li>)}</ul> : <p className="ag-muted">None scheduled.</p>}
          </Section>
          <Section title="Tasks">
            {p.tasks?.length ? <ul className="ag-linklist">{p.tasks.map(t => <li key={t.id}>{t.title} <Pill value={t.status} /> <span className="ag-muted ag-small">{t.due_at ? `due ${fmtDate(t.due_at)}` : ''}</span></li>)}</ul> : <Empty>No tasks.</Empty>}
          </Section>
        </div>
      </div>
    </AgencyPage>
  )
}
