/* CLIENT GROWTH WORKSPACE (/client-growth) - EvoSys Pro platform capability.
 *
 * Opened from its own Program Access card. Every list, count and action comes
 * from /client-growth/*, which re-checks the signed-in person's access on each
 * request (God Mode, the sales program owners, or the client_growth grant).
 * Prospects are the existing EvoSys Pro Sales records; nothing here is a second
 * CRM. Creating or copying a link never sends anything to anybody.
 *
 * Design: the approved Agency Growth Questionnaire look (navy / gold / white,
 * Playfair headings) carried into the staff screens. Every row shows its status
 * and the obvious next step; on a phone, tables become stacked cards.
 */
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { launcherOrigin } from '../program/launcherOrigin.js'
import './clientGrowth.css'

const TABS = [
  ['overview', 'Overview'], ['prospects', 'Prospects'], ['questionnaires', 'Questionnaires'],
  ['tasks', 'Proposal tasks'], ['templates', 'Templates'],
]

export function fmt (iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric',
      year: 'numeric', hour: 'numeric', minute: '2-digit' }) + ' CT'
  } catch { return iso }
}

function fmtDay (iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric' }) } catch { return iso }
}

function errText (e) {
  const d = e && e.detail
  if (typeof d === 'string') return d
  if (d && d.message) return d.message
  return (e && e.message) || 'Something went wrong.'
}

/* ── small building blocks ───────────────────────────────────────────────── */

export function Badge ({ children, tone }) {
  return <span className={'cg-chip' + (tone ? ' cg-chip--' + tone : '')}><span className="cg-chip-dot" aria-hidden="true" />{children}</span>
}

const INV_TONE = { active: 'navy', sent: 'info', opened: 'gold', submitted: 'ok', revoked: 'bad', expired: 'muted' }
const INV_LABEL = { active: 'Link created · not sent', sent: 'Sent (you recorded it)', opened: 'Opened by prospect',
  submitted: 'Answered', revoked: 'Revoked', expired: 'Expired' }
const SUB_TONE = { submitted: 'gold', under_review: 'info', proposal_prepared: 'ok' }

function TestTag ({ on }) { return on ? <span className="cg-test">Test</span> : null }

function Section ({ title, action, children, sub }) {
  return (
    <section className="cg-section">
      <div className="cg-section-head">
        <div><h2 className="cg-h2">{title}</h2>{sub ? <p className="cg-section-sub">{sub}</p> : null}</div>
        {action || null}
      </div>
      {children}
    </section>
  )
}

function Empty ({ children, action }) {
  return <div className="cg-empty"><p>{children}</p>{action || null}</div>
}

function useLoad (fn, deps) {
  const [state, setState] = useState({ loading: true })
  const load = useCallback(() => {
    setState(s => ({ ...s, loading: true, error: null }))
    return fn().then(data => setState({ loading: false, data })).catch(e => setState({ loading: false, error: errText(e) }))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => { load() }, [load])
  return [state, load]
}

function Loading ({ state, retry, children }) {
  if (state.loading && !state.data) return <div className="cg-skeleton" role="status" aria-live="polite"><span className="cg-spin" aria-hidden="true" />Loading…</div>
  if (state.error) return <div className="cg-alert" role="alert">{state.error} <button className="cg-link" onClick={retry}>Try again</button></div>
  return children
}

function CopyButton ({ invId, url, primary, onDone }) {
  const [msg, setMsg] = useState('')
  const copy = async () => {
    try {
      const r = url ? { url } : await api.post(`/client-growth/invitations/${invId}/copy`, {})
      await navigator.clipboard.writeText(r.url)
      setMsg('Copied')
    } catch (e) {
      setMsg(e && e.detail ? errText(e) : 'Copy failed — select the link and copy it')
    }
    setTimeout(() => setMsg(''), 2500)
    onDone && onDone()
  }
  return (
    <span className="cg-copy">
      <button className={'cg-btn cg-btn--sm' + (primary ? ' cg-btn--gold' : '')} onClick={copy}>Copy link</button>
      {msg ? <span className="cg-copied" role="status">{msg}</span> : null}
    </span>
  )
}

function previewHref (inv) {
  return '/client-growth/preview/' + inv.template.id + '?for=' + encodeURIComponent(inv.prepared_for || '')
    + '&contact_name=' + encodeURIComponent(inv.contact || '') + '&business_name=' + encodeURIComponent(inv.company || '')
}

/* ── overview ────────────────────────────────────────────────────────────── */

const TILE_ICONS = {
  link: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></svg>,
  inbox: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z" /></svg>,
  eye: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></svg>,
  check: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3" /></svg>,
}

function Overview ({ openSubmission, setTab }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/overview'), [])
  const c = st.data?.counts || {}
  const t = st.data?.test_counts || {}
  const tiles = [
    ['link', 'Pending invitations', c.pending_invitations, 'Links not answered yet', 'questionnaires'],
    ['inbox', 'Submitted questionnaires', c.submitted, 'Answers received', 'questionnaires'],
    ['eye', 'Awaiting review', c.awaiting_review, 'Nobody has started review', 'questionnaires'],
    ['check', 'Proposals prepared', c.proposals_prepared, 'Marked prepared by staff', 'tasks'],
  ]
  const testsHeld = Math.max(0, (t.submitted || 0) - (c.submitted || 0)) + Math.max(0, (t.pending_invitations || 0) - (c.pending_invitations || 0))
  return (
    <Loading state={st} retry={reload}>
      <div className="cg-tiles">
        {tiles.map(([icon, label, n, hint, tab]) => (
          <button key={label} className={'cg-tile' + (label === 'Awaiting review' && n ? ' cg-tile--alert' : '')} onClick={() => setTab(tab)}>
            <span className="cg-tile-icon">{TILE_ICONS[icon]}</span>
            <span className="cg-tile-n">{n ?? 0}</span>
            <span className="cg-tile-l">{label}</span>
            <span className="cg-tile-h">{hint}</span>
          </button>
        ))}
      </div>
      {testsHeld > 0 ? <p className="cg-note">Synthetic test records are left out of these counts ({testsHeld}).</p> : null}

      <Section title="Needs attention" sub="Submitted answers waiting on your team, oldest first.">
        {(st.data?.attention || []).length === 0 ? <Empty>Nothing waiting for review.</Empty> : (
          <SubmissionTable rows={st.data.attention} openSubmission={openSubmission} reload={reload} />
        )}
      </Section>

      <Section title="Recent links" action={<button className="cg-btn cg-btn--gold" onClick={() => setTab('prospects')}>+ New questionnaire link</button>}>
        {(st.data?.recent_invitations || []).length === 0
          ? <Empty action={<button className="cg-btn cg-btn--gold" onClick={() => setTab('prospects')}>Choose a prospect</button>}>No questionnaire links yet.</Empty>
          : <InvitationTable rows={st.data.recent_invitations} reload={reload} openSubmission={openSubmission} compact />}
      </Section>
    </Loading>
  )
}

/* ── prospects ───────────────────────────────────────────────────────────── */

function Field ({ label, optional, children }) {
  return <label className="cg-field"><span className="cg-field-l">{label}{optional ? <span className="cg-opt"> (optional)</span> : null}</span>{children}</label>
}

function NewProspect ({ onCreated }) {
  const [f, setF] = useState({ company: '', contact: '', email: '', phone: '', industry: '' })
  const [dups, setDups] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const save = async (confirmNew) => {
    setErr(''); setBusy(true)
    try {
      const r = await api.post('/client-growth/prospects', { ...f, confirm_new: !!confirmNew })
      setDups(null); onCreated(r.prospect)
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.duplicates) setDups(e.detail.duplicates)
      else setErr(errText(e))
    } finally { setBusy(false) }
  }
  return (
    <div className="cg-card cg-card--form">
      <h3 className="cg-h3">Add a prospect</h3>
      <p className="cg-muted">Checked against existing EvoSys Pro Sales prospects first. New prospects start in Discovery and are not added to any outreach.</p>
      <div className="cg-form">
        <Field label="Business name"><input value={f.company} onChange={set('company')} maxLength={200} /></Field>
        <Field label="Primary contact"><input value={f.contact} onChange={set('contact')} maxLength={200} /></Field>
        <Field label="Email"><input type="email" value={f.email} onChange={set('email')} maxLength={160} /></Field>
        <Field label="Phone" optional><input value={f.phone} onChange={set('phone')} maxLength={25} /></Field>
        <Field label="Industry" optional><input value={f.industry} onChange={set('industry')} placeholder="insurance" maxLength={60} /></Field>
      </div>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      {dups ? (
        <div className="cg-alert cg-alert--warn" role="alert">
          <strong>This may already be a prospect.</strong>
          <ul>{dups.map(d => <li key={d.id}>{d.company} — {d.contact || 'no contact'} · {d.email || 'no email'} · {d.stage_label}
            {' '}<button className="cg-link" onClick={() => onCreated(d)}>Use this record</button></li>)}</ul>
          <button className="cg-btn cg-btn--sm" disabled={busy} onClick={() => save(true)}>It's a different business — create it</button>
        </div>
      ) : null}
      <button className="cg-btn cg-btn--gold cg-btn--block" disabled={busy || !f.company.trim()} onClick={() => save(false)}>Add prospect</button>
    </div>
  )
}

function InviteForm ({ prospect, onCreated }) {
  const [tpl, setTpl] = useState(null)
  const [f, setF] = useState({ template_key: '', prepared_for: prospect.contact || '', contact_name: prospect.contact || '',
    business_name: prospect.company || '', email: prospect.email || '', expires_days: 30 })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get('/client-growth/templates').then(r => {
      const act = r.templates.filter(t => t.is_active)
      setTpl(act)
      if (act.length) setF(x => ({ ...x, template_key: x.template_key || act[act.length - 1].key }))
    }).catch(e => setErr(errText(e)))
  }, [])
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const chosen = (tpl || []).find(t => t.key === f.template_key)
  const go = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/client-growth/invitations', { prospect_id: prospect.id, ...f, expires_days: Number(f.expires_days) || 30 })
      onCreated(r.invitation)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="cg-card cg-card--form">
      <h3 className="cg-h3">Create questionnaire link</h3>
      <p className="cg-muted">Creates a private link only. Nothing is emailed or texted — copy it and send it yourself.</p>
      <div className="cg-form">
        <Field label="Template"><select value={f.template_key} onChange={set('template_key')}>
          {(tpl || []).map(t => <option key={t.id} value={t.key}>{t.name} (v{t.version})</option>)}</select></Field>
        <Field label={'"Prepared for" name'}><input value={f.prepared_for} onChange={set('prepared_for')} maxLength={120} /></Field>
        <Field label="Pre-filled contact name"><input value={f.contact_name} onChange={set('contact_name')} maxLength={160} /></Field>
        <Field label="Pre-filled business name"><input value={f.business_name} onChange={set('business_name')} maxLength={160} /></Field>
        <Field label="Pre-filled email"><input type="email" value={f.email} onChange={set('email')} maxLength={160} /></Field>
        <Field label="Expires after (days)"><input type="number" min="1" max="180" value={f.expires_days} onChange={set('expires_days')} /></Field>
      </div>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      <div className="cg-actions">
        <button className="cg-btn cg-btn--gold" disabled={busy || !f.template_key} onClick={go}>Create link</button>
        {chosen ? <Link className="cg-btn" to={'/client-growth/preview/' + chosen.id + '?for=' + encodeURIComponent(f.prepared_for)
          + '&contact_name=' + encodeURIComponent(f.contact_name) + '&business_name=' + encodeURIComponent(f.business_name)
          + '&email=' + encodeURIComponent(f.email)}>Preview form</Link> : null}
      </div>
    </div>
  )
}

function ProspectDetail ({ id, openSubmission, onBack }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/prospects/' + id), [id])
  const [created, setCreated] = useState(null)
  const p = st.data?.prospect
  return (
    <Loading state={st} retry={reload}>
      {p ? (
        <div>
          <button className="cg-back" onClick={onBack}>← All prospects</button>
          <div className="cg-profile">
            <div className="cg-avatar" aria-hidden="true">{(p.company || '?').split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase()}</div>
            <div>
              <h2 className="cg-h2 cg-h2--tight">{p.company} <TestTag on={p.is_test} /></h2>
              <p className="cg-sub">{p.contact || 'No contact'} · {p.email || 'no email'}{p.phone ? ' · ' + p.phone : ''}</p>
            </div>
            <div className="cg-profile-meta"><Badge tone="navy">{p.stage_label}</Badge><span className="cg-sub">Source: {p.source || '—'}</span></div>
          </div>
          {created ? (
            <div className="cg-alert cg-alert--ok" role="status">
              <strong>Link created — not sent.</strong> Copy it and send it yourself.
              <div className="cg-linkbox"><code>{created.url}</code><CopyButton url={created.url} primary /></div>
            </div>
          ) : null}
          <div className="cg-two">
            <InviteForm prospect={p} onCreated={(i) => { setCreated(i); reload() }} />
            <div className="cg-card">
              <h3 className="cg-h3">Activity history</h3>
              {st.data.activity.length ? (
                <ol className="cg-timeline">{st.data.activity.map((a, i) => (
                  <li key={i}><span className="cg-tl-dot" aria-hidden="true" /><div><div className="cg-tl-when">{fmt(a.at)}</div>
                    <div className="cg-tl-what">{a.summary}</div>{a.detail ? <div className="cg-sub">{a.detail}</div> : null}</div></li>))}</ol>
              ) : <p className="cg-muted">No activity yet.</p>}
            </div>
          </div>
          <Section title="Links">
            {st.data.invitations.length ? <InvitationTable rows={st.data.invitations} reload={reload} openSubmission={openSubmission} /> : <Empty>None yet.</Empty>}
          </Section>
          <Section title="Submissions">
            {st.data.submissions.length ? <SubmissionTable rows={st.data.submissions} openSubmission={openSubmission} reload={reload} /> : <Empty>None yet.</Empty>}
          </Section>
        </div>
      ) : null}
    </Loading>
  )
}

function Prospects ({ openSubmission, initialId }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [sel, setSel] = useState(initialId || null)
  const [st, reload] = useLoad(() => api.get('/client-growth/prospects', { params: term ? { q: term } : {} }), [term])
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t) }, [q])
  if (sel) return <ProspectDetail id={sel} openSubmission={openSubmission} onBack={() => { setSel(null); reload() }} />
  return (
    <div className="cg-two cg-two--wide">
      <div>
        <label className="cg-search">
          <span className="cg-field-l">Find an existing EvoSys Pro Sales prospect</span>
          <span className="cg-search-box">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
            <input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Business, contact or email" />
          </span>
        </label>
        <Loading state={st} retry={reload}>
          {(st.data?.prospects || []).length === 0 ? <Empty>{term ? 'No matching prospects.' : 'No prospects in discovery yet. Search above, or add one.'}</Empty> : (
            <div className="cg-table-wrap">
              <table className="cg-table"><thead><tr><th>Business</th><th>Contact</th><th>Stage</th><th>Questionnaires</th><th>Next step</th></tr></thead><tbody>
                {st.data.prospects.map(p => (
                  <tr key={p.id}>
                    <td data-label="Business"><strong>{p.company}</strong> <TestTag on={p.is_test} /></td>
                    <td data-label="Contact">{p.contact || '—'}<div className="cg-sub">{p.email}</div></td>
                    <td data-label="Stage"><Badge tone="navy">{p.stage_label}</Badge></td>
                    <td data-label="Questionnaires">{p.invitations ? `${p.invitations} link${p.invitations > 1 ? 's' : ''} · ${p.submissions} answered` : 'None yet'}</td>
                    <td data-label="Next step" className="cg-next"><button className="cg-btn cg-btn--sm cg-btn--gold" onClick={() => setSel(p.id)}>{p.invitations ? 'Open' : 'Create link'}</button></td>
                  </tr>
                ))}
              </tbody></table>
            </div>
          )}
        </Loading>
      </div>
      <NewProspect onCreated={p => setSel(p.id)} />
    </div>
  )
}

/* ── questionnaires ──────────────────────────────────────────────────────── */

function RevokeBox ({ inv, onDone, onCancel }) {
  const [reason, setReason] = useState('')
  const [err, setErr] = useState('')
  const go = async () => {
    try { await api.post(`/client-growth/invitations/${inv.id}/revoke`, { reason, confirm: true }); onDone() } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="cg-confirm" role="dialog" aria-label="Revoke link">
      <p><strong>Revoke the link for {inv.company}?</strong> It stops working immediately. This is recorded in the audit log.</p>
      <Field label="Reason"><input value={reason} onChange={e => setReason(e.target.value)} maxLength={500} autoFocus /></Field>
      {err ? <div className="cg-alert" role="alert">{err}</div> : null}
      <div className="cg-actions">
        <button className="cg-btn cg-btn--danger" disabled={!reason.trim()} onClick={go}>Revoke link</button>
        <button className="cg-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function InvitationTable ({ rows, reload, openSubmission, compact }) {
  const [revoking, setRevoking] = useState(null)
  const [msg, setMsg] = useState(null)
  const markSent = async (i) => {
    try { await api.post(`/client-growth/invitations/${i.id}/mark-sent`, { note: null }); reload() } catch (e) { setMsg(errText(e)) }
  }
  const reissue = async (i) => {
    try { const r = await api.post(`/client-growth/invitations/${i.id}/reissue`, { reason: 'Replaced with a new link' }); setMsg({ url: r.invitation.url }); reload() } catch (e) { setMsg(errText(e)) }
  }
  return (
    <div>
      {msg ? (typeof msg === 'string' ? <div className="cg-alert">{msg}</div>
        : <div className="cg-alert cg-alert--ok">New link created — not sent.<div className="cg-linkbox"><code>{msg.url}</code><CopyButton url={msg.url} primary /></div></div>) : null}
      {revoking ? <RevokeBox inv={revoking} onCancel={() => setRevoking(null)} onDone={() => { setRevoking(null); reload() }} /> : null}
      <div className="cg-table-wrap">
        <table className="cg-table">
          <thead><tr><th>Prospect</th>{compact ? null : <th>Template</th>}<th>Status</th><th>Created</th>{compact ? null : <th>Expires</th>}<th>Next step</th></tr></thead>
          <tbody>
            {rows.map(i => {
              const live = ['active', 'sent', 'opened'].includes(i.state)
              return (
                <tr key={i.id}>
                  <td data-label="Prospect"><strong>{i.company}</strong> <TestTag on={i.is_test} /><div className="cg-sub">for {i.prepared_for || i.contact || '—'}</div></td>
                  {compact ? null : <td data-label="Template">{i.template ? <>{i.template.name}<div className="cg-sub">version {i.template.version}</div></> : '—'}</td>}
                  <td data-label="Status"><Badge tone={INV_TONE[i.state]}>{INV_LABEL[i.state] || i.state_label}</Badge>
                    {i.sent_at ? <div className="cg-sub">You recorded it sent {fmtDay(i.sent_at)}</div> : null}
                    {i.opened_at && i.state !== 'submitted' ? <div className="cg-sub">Opened {fmt(i.opened_at)}</div> : null}</td>
                  <td data-label="Created">{fmt(i.created_at)}</td>
                  {compact ? null : <td data-label="Expires">{fmtDay(i.expires_at)}</td>}
                  <td data-label="Next step" className="cg-next">
                    <div className="cg-actions">
                      {live ? <CopyButton invId={i.id} primary={!i.sent_at} /> : null}
                      {live && !i.sent_at ? <button className="cg-btn cg-btn--sm" onClick={() => markSent(i)} title="Record that you sent it yourself">I sent it</button> : null}
                      {i.submission_id ? <button className="cg-btn cg-btn--sm cg-btn--gold" onClick={() => openSubmission(i.submission_id)}>Review answers</button> : null}
                      {!live && !i.submission_id ? <button className="cg-btn cg-btn--sm cg-btn--gold" onClick={() => reissue(i)} title="A fresh link; earlier answers stay as received">New link</button> : null}
                      {i.template && i.template.id ? <Link className="cg-btn cg-btn--sm cg-btn--ghost" to={previewHref(i)} title="See the form as this prospect will — their link is not opened">Preview</Link> : null}
                      {live ? <button className="cg-btn cg-btn--sm cg-btn--quiet-danger" onClick={() => setRevoking(i)}>Revoke</button> : null}
                      {i.state === 'submitted' && !compact ? <button className="cg-btn cg-btn--sm cg-btn--ghost" onClick={() => reissue(i)} title="Correction / re-invitation: a fresh link; earlier answers stay as received">New link</button> : null}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="cg-note">Delivery is never known here: a link being created or copied is not proof it was sent or received.</p>
    </div>
  )
}

function SubmissionTable ({ rows, openSubmission, reload }) {
  const [busy, setBusy] = useState('')
  const start = async (s) => {
    setBusy(s.id)
    try { await api.post(`/client-growth/submissions/${s.id}/review`, {}); reload && reload() } catch { /* the panel shows errors */ } finally { setBusy('') }
    openSubmission(s.id)
  }
  return (
    <div className="cg-table-wrap">
      <table className="cg-table"><thead><tr><th>Prospect</th><th>Template</th><th>Submitted</th><th>Status</th><th>Next step</th></tr></thead><tbody>
        {rows.map(s => (
          <tr key={s.id}>
            <td data-label="Prospect"><strong>{s.company}</strong> <TestTag on={s.is_test} /><div className="cg-sub">{s.contact}</div></td>
            <td data-label="Template">{s.template_name || s.template_key}<div className="cg-sub">version {s.template_version}</div></td>
            <td data-label="Submitted">{fmt(s.submitted_at)}</td>
            <td data-label="Status"><Badge tone={SUB_TONE[s.status]}>{s.status_label}</Badge></td>
            <td data-label="Next step" className="cg-next">
              {s.status === 'submitted' ? <button className="cg-btn cg-btn--sm cg-btn--gold" disabled={busy === s.id} onClick={() => start(s)}>Start review</button>
                : s.status === 'under_review' ? <button className="cg-btn cg-btn--sm cg-btn--gold" onClick={() => openSubmission(s.id)}>Finish &amp; mark prepared</button>
                  : <button className="cg-btn cg-btn--sm" onClick={() => openSubmission(s.id)}>View answers</button>}
            </td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}

function Questionnaires ({ openSubmission }) {
  const [inv, reloadInv] = useLoad(() => api.get('/client-growth/invitations'), [])
  const [sub, reloadSub] = useLoad(() => api.get('/client-growth/submissions'), [])
  return (
    <div>
      <Section title="Submissions" sub="Original answers from prospects. Open one to review it and prepare the proposal.">
        <Loading state={sub} retry={reloadSub}>
          {(sub.data?.submissions || []).length ? <SubmissionTable rows={sub.data.submissions} openSubmission={openSubmission} reload={reloadSub} /> : <Empty>No submissions yet.</Empty>}
        </Loading>
      </Section>
      <Section title="Invitation links" sub="Private links you've created. Copy one, send it yourself, then record it as sent.">
        <Loading state={inv} retry={reloadInv}>
          {(inv.data?.invitations || []).length ? <InvitationTable rows={inv.data.invitations} reload={reloadInv} openSubmission={openSubmission} /> : <Empty>No links yet.</Empty>}
        </Loading>
      </Section>
    </div>
  )
}

export function SubmissionView ({ id, onClose, onOpenProspect }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/submissions/' + id), [id])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [raw, setRaw] = useState(false)
  const s = st.data?.submission
  const act = async (path, label) => {
    setBusy(label); setErr('')
    try { await api.post(`/client-growth/submissions/${id}/${path}`, {}); await reload() } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const groups = useMemo(() => {
    const g = []
    for (const r of s?.readable || []) {
      const last = g[g.length - 1]
      if (!last || last.step !== r.step) g.push({ step: r.step, rows: [r] }); else last.rows.push(r)
    }
    return g
  }, [s])
  return (
    <div className="cg-sheet" role="dialog" aria-modal="true" aria-label="Submitted answers" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cg-sheet-inner">
        <div className="cg-sheet-head">
          <div>
            <p className="cg-eyebrow cg-eyebrow--gold">Submitted questionnaire</p>
            <h2 className="cg-sheet-title">{s ? s.company : 'Loading…'} {s ? <TestTag on={s.is_test} /> : null}</h2>
            {s ? <p className="cg-sheet-sub">Submitted {fmt(s.submitted_at)} · {s.template_name || s.template_key} v{s.template_version}</p> : null}
          </div>
          <button className="cg-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="cg-sheet-body">
          <Loading state={st} retry={reload}>
            {s ? <>
              <ol className="cg-steps" aria-label="Review progress">
                {[['submitted', 'Received'], ['under_review', 'Under review'], ['proposal_prepared', 'Proposal prepared']].map(([k, l], i) => {
                  const order = ['submitted', 'under_review', 'proposal_prepared']
                  const done = order.indexOf(s.status) >= i
                  return <li key={k} className={done ? 'is-done' : ''}><span>{i + 1}</span>{l}</li>
                })}
              </ol>
              <div className="cg-nextbar">
                {s.status === 'submitted' ? <button className="cg-btn cg-btn--gold" disabled={!!busy} onClick={() => act('review', 'review')}>Start review</button> : null}
                {s.status !== 'proposal_prepared' ? <button className={'cg-btn' + (s.status === 'under_review' ? ' cg-btn--gold' : '')} disabled={!!busy} onClick={() => act('proposal-prepared', 'prep')}>Mark proposal prepared</button> : <Badge tone="ok">Proposal prepared {fmtDay(s.proposal_prepared_at)}</Badge>}
                <button className="cg-btn cg-btn--ghost" disabled={!!busy} onClick={() => act('ai-summary', 'ai')}>{busy === 'ai' ? 'Working…' : (s.ai_summary ? 'Redo AI summary' : 'AI summary')}</button>
                <button className="cg-btn cg-btn--ghost" onClick={() => onOpenProspect(s.prospect_id)}>Open prospect</button>
              </div>
              {err ? <div className="cg-alert" role="alert">{err}</div> : null}
              {s.task ? (
                <div className="cg-card cg-task" id="task">
                  <div><span className="cg-eyebrow">Proposal task</span><div className="cg-task-title">{s.task.title}</div>
                    <div className="cg-sub">Created {fmt(s.task.created_at)}{s.task.completed_at ? ' · done ' + fmt(s.task.completed_at) : ''}</div></div>
                  <Badge tone={s.task.status === 'done' ? 'ok' : 'gold'}>{s.task.status === 'done' ? 'Done' : 'Open'}</Badge>
                </div>
              ) : null}
              {s.ai_summary ? (
                <div className="cg-card cg-ai">
                  <p className="cg-ai-label">{s.ai_summary.label}</p>
                  {s.ai_summary.status === 'done' ? <dl>
                    <dt>Goals</dt><dd>{s.ai_summary.content.goals || '—'}</dd>
                    <dt>Budget</dt><dd>{s.ai_summary.content.budget || '—'}</dd>
                    <dt>VA readiness</dt><dd>{s.ai_summary.content.va_readiness || '—'}</dd>
                    <dt>Still to ask</dt><dd>{(s.ai_summary.content.missing || []).join('; ') || '—'}</dd>
                  </dl> : <p className="cg-muted">{s.ai_summary.error}</p>}
                </div>
              ) : null}
              <h3 className="cg-h3">Original answers</h3>
              {groups.map((g, gi) => (
                <div key={g.step} className="cg-answers">
                  <h4><span className="cg-num">{String(gi + 1).padStart(2, '0')}</span>{g.step}</h4>
                  <dl>{g.rows.map(r => <div key={r.key}><dt>{r.label}</dt><dd>{Array.isArray(r.display) ? r.display.join(', ') : String(r.display)}</dd></div>)}</dl>
                </div>
              ))}
              <button className="cg-link" onClick={() => setRaw(!raw)}>{raw ? 'Hide' : 'Show'} exactly as received</button>
              {raw ? <pre className="cg-pre">{JSON.stringify(s.answers, null, 2)}</pre> : null}
            </> : null}
          </Loading>
        </div>
      </div>
    </div>
  )
}

/* ── tasks and templates ─────────────────────────────────────────────────── */

function Tasks ({ openSubmission }) {
  const [st, reload] = useLoad(() => api.get('/client-growth/tasks'), [])
  return (
    <Section title="Proposal tasks" sub="Each submitted questionnaire creates one task. It closes when the proposal is marked prepared.">
      <Loading state={st} retry={reload}>
        {(st.data?.tasks || []).length === 0 ? <Empty>No proposal tasks yet. A task is created when a questionnaire is submitted.</Empty> : (
          <div className="cg-table-wrap">
            <table className="cg-table"><thead><tr><th>Task</th><th>Prospect</th><th>Created</th><th>Status</th><th>Next step</th></tr></thead><tbody>
              {st.data.tasks.map(k => (
                <tr key={k.id}>
                  <td data-label="Task"><strong>{k.title}</strong> <TestTag on={k.is_test} /></td>
                  <td data-label="Prospect">{k.company}</td>
                  <td data-label="Created">{fmt(k.created_at)}</td>
                  <td data-label="Status"><Badge tone={k.status === 'done' ? 'ok' : 'gold'}>{k.status === 'done' ? 'Proposal prepared' : 'Open'}</Badge></td>
                  <td data-label="Next step" className="cg-next"><button className={'cg-btn cg-btn--sm' + (k.status === 'done' ? '' : ' cg-btn--gold')} onClick={() => openSubmission(k.submission_id)}>{k.status === 'done' ? 'View answers' : 'Work on proposal'}</button></td>
                </tr>
              ))}
            </tbody></table>
          </div>
        )}
      </Loading>
    </Section>
  )
}

const FIELD_KIND = { count_or_help: 'Number or "help me"', amount_or_help: 'Amount or "not sure"', select: 'Dropdown', radio: 'Pick one',
  multi: 'Pick any', text: 'Text', textarea: 'Long text', email: 'Email', tel: 'Phone', int: 'Number' }

function Templates () {
  const [st, reload] = useLoad(() => api.get('/client-growth/templates'), [])
  const [open, setOpen] = useState(null)
  const [def, setDef] = useState(null)
  useEffect(() => { if (open) api.get('/client-growth/templates/' + open).then(r => setDef(r.definition)).catch(() => setDef(null)) }, [open])
  return (
    <Section title="Questionnaire templates" sub="Shared discovery questions (goals, budget, team, contact, notes) plus questions for each kind of business. Every submission keeps the version it was answered on.">
      <Loading state={st} retry={reload}>
        <div className="cg-tpl-grid">
          {(st.data?.templates || []).map(t => (
            <div className="cg-card cg-tpl" key={t.id}>
              <div className="cg-tpl-top">
                <div><p className="cg-eyebrow">{t.vertical || 'Any business'} · version {t.version}</p><h3 className="cg-tpl-name">{t.name}</h3></div>
                <Badge tone={t.is_active ? 'ok' : 'muted'}>{t.is_active ? 'Active' : 'Off'}</Badge>
              </div>
              <ul className="cg-tpl-steps">{t.steps.map((s, i) => <li key={s.key}><span className="cg-num">{String(i + 1).padStart(2, '0')}</span>{s.title}<span className="cg-sub">{s.questions} questions</span></li>)}</ul>
              <p className="cg-sub">{t.invitations} link{t.invitations === 1 ? '' : 's'} created with this template</p>
              <div className="cg-actions">
                <Link className="cg-btn cg-btn--gold" to={'/client-growth/preview/' + t.id}>Preview form</Link>
                <button className="cg-btn" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>{open === t.id ? 'Hide questions' : 'See all questions'}</button>
              </div>
            </div>
          ))}
        </div>
        {open && def ? (
          <div className="cg-card cg-qlist">
            {def.steps.map(s => (
              <div key={s.key}>
                <h4><span className="cg-num">{s.number}</span>{s.title}</h4>
                <ul>{s.fields.map(f => (
                  <li key={f.key}><span>{f.label}</span>
                    <span className="cg-qtags"><span className="cg-qtag">{FIELD_KIND[f.type] || f.type}</span>
                      {f.required ? <span className="cg-qtag cg-qtag--req">Required</span> : <span className="cg-qtag">Optional</span>}
                      {f.template_specific ? <span className="cg-qtag cg-qtag--gold">This template only</span> : null}
                      {f.show_if ? <span className="cg-qtag">Shown when relevant</span> : null}</span></li>))}</ul>
              </div>
            ))}
          </div>
        ) : null}
      </Loading>
    </Section>
  )
}

/* ── shell ───────────────────────────────────────────────────────────────── */

export default function ClientGrowthWorkspace () {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [me, setMe] = useState(null)
  const [denied, setDenied] = useState('')
  const [counts, setCounts] = useState(null)
  const tab = params.get('tab') || 'overview'
  const subId = params.get('submission')
  const prospectId = params.get('prospect')
  useEffect(() => { document.title = 'Client Growth · EvoSys Pro' }, [])
  useEffect(() => {
    api.get('/client-growth/me').then(setMe).catch(e => setDenied(errText(e)))
  }, [])
  useEffect(() => { api.get('/client-growth/overview').then(r => setCounts(r.counts)).catch(() => {}) }, [tab, subId])
  const setTab = (t) => setParams(t === 'overview' ? {} : { tab: t })
  const openSubmission = (id) => setParams({ ...(tab !== 'overview' ? { tab } : {}), submission: id })
  const openProspect = (id) => setParams({ tab: 'prospects', prospect: id })
  const back = launcherOrigin() || '/workspaces'
  const tabCount = { questionnaires: counts?.awaiting_review, tasks: counts ? Math.max(0, (counts.submitted || 0) - (counts.proposals_prepared || 0)) : 0 }

  if (denied) {
    return <div className="cg-page"><div className="cg-denied" role="alert"><h1>Client Growth</h1><p>{denied}</p><Link className="cg-btn cg-btn--gold" to="/workspaces">Back to Program Access</Link></div></div>
  }
  return (
    <div className="cg-page">
      <header className="cg-top">
        <div className="cg-brand">
          <img src="/brands/evosyspro-emblem-96.png" alt="" width="36" height="36" />
          <span className="cg-bar" aria-hidden="true" />
          <span className="cg-brand-name">EvoSys Pro</span>
        </div>
        <nav className="cg-topnav" aria-label="Client Growth">
          {me && me.is_owner ? <Link to="/god/client-growth" className="cg-toplink">God Mode</Link> : null}
          <button className="cg-toplink" onClick={() => navigate(back)}>Program Access</button>
        </nav>
      </header>
      <div className="cg-hero">
        <div className="cg-hero-inner">
          <div>
            <p className="cg-hero-eyebrow">EvoSys Pro platform</p>
            <h1 className="cg-hero-title">Client Growth</h1>
            <p className="cg-hero-sub">Discovery • Questionnaires • Proposals</p>
          </div>
          <div className="cg-hero-actions">
            <button className="cg-btn cg-btn--gold" onClick={() => setTab('prospects')}>+ New questionnaire link</button>
            <button className="cg-btn cg-btn--onnavy" onClick={() => setTab('templates')}>Preview form</button>
          </div>
        </div>
        <nav className="cg-tabs" role="tablist" aria-label="Sections">
          {TABS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={'cg-tab' + (tab === k ? ' is-on' : '')} onClick={() => setTab(k)}>
              {label}{tabCount[k] ? <span className="cg-tab-count" aria-label={tabCount[k] + ' need attention'}>{tabCount[k]}</span> : null}
            </button>
          ))}
        </nav>
      </div>
      <main className="cg-wrap">
        {tab === 'overview' ? <Overview openSubmission={openSubmission} setTab={setTab} /> : null}
        {tab === 'prospects' ? <Prospects key={prospectId || 'list'} openSubmission={openSubmission} initialId={prospectId} /> : null}
        {tab === 'questionnaires' ? <Questionnaires openSubmission={openSubmission} /> : null}
        {tab === 'tasks' ? <Tasks openSubmission={openSubmission} /> : null}
        {tab === 'templates' ? <Templates /> : null}
      </main>
      {subId ? <SubmissionView id={subId} onClose={() => setParams(tab !== 'overview' ? { tab } : {})} onOpenProspect={openProspect} /> : null}
    </div>
  )
}
