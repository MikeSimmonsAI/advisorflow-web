/**
 * DEMO — what a salesperson needs to know, and nothing they don't.
 *
 * WHAT THIS REPLACES. A five-field admin form: a status dropdown, a builder
 * dropdown, a target date, a full-width REQUIREMENTS textarea and a full-width
 * INTERNAL NOTES textarea, all open, all the time, to everybody. That is an
 * engineering work order. A rep opening their deal wants one sentence: is my
 * demo being built, by whom, by when, and can I open it.
 *
 * WHO SEES THE CONTROLS. `opp.can_manage_demo` is the SERVER's answer — the
 * manager, or the person actually assigned to build this one. Everyone else
 * gets the outcome. Hiding a control is a courtesy, not the access control:
 * PATCH is gated by `assert_can_edit_opportunity` either way.
 */
import { useEffect, useState } from 'react'
import { Card, Chip, Info, Empty, dateTime } from './parts'

const DEMO_STATUSES = [
  ['', '—'],
  ['not_requested', 'Not requested'],
  ['requested', 'Requested'],
  ['in_progress', 'In progress'],
  ['ready', 'Ready'],
  ['delivered', 'Delivered'],
]

const STATUS_LABEL = {
  not_requested: 'Not requested', requested: 'Requested',
  in_progress: 'In progress', ready: 'Ready', delivered: 'Delivered',
}

const STATUS_TONE = {
  ready: 'green', delivered: 'green', in_progress: 'amber', requested: 'amber',
}

export default function DemoPanel({ opp, team, onPatch, onRequest, saving }) {
  const d = opp.demo || {}
  const mayManage = !!opp.can_manage_demo
  const requested = !!d.requested_at || (d.status && d.status !== 'not_requested')

  // The seller's brief to whoever builds it. Behind a disclosure rather than
  // on the page: it is written once, near the discovery call, and re-read far
  // more often than it is edited.
  const [reqOpen, setReqOpen] = useState(false)
  const [reqs, setReqs] = useState('')
  useEffect(() => { setReqs(d.requirements || '') }, [opp.id, d.requirements])

  const [form, setForm] = useState({})
  useEffect(() => {
    setForm({
      demo_status: d.status || '',
      demo_owner_user_id: d.owner_user_id || '',
      demo_due_at: d.due_at ? String(d.due_at).slice(0, 10) : '',
      demo_url: d.url || '',
      demo_notes: d.notes || '',
    })
  }, [opp.id, d.status, d.owner_user_id, d.due_at, d.url, d.notes])

  function saveInternal() {
    const body = { ...form }
    body.demo_due_at = form.demo_due_at ? new Date(form.demo_due_at).toISOString() : null
    if (!body.demo_status) delete body.demo_status
    if (!body.demo_owner_user_id) body.demo_owner_user_id = null
    onPatch(body)
  }

  const latest = d.ready_at
    ? 'Ready ' + dateTime(d.ready_at)
    : (d.requested_at ? 'Requested ' + dateTime(d.requested_at) : 'Nothing yet')

  return (
    <Card
      title="DEMO"
      sub="Where the build is, without asking anyone"
      right={d.status
        ? <Chip tone={STATUS_TONE[d.status]}>{STATUS_LABEL[d.status] || d.status}</Chip>
        : <Chip>Not requested</Chip>}
    >
      {!requested ? (
        <Empty title="No demo requested yet">
          Requesting one moves this deal to Demo Build and hands the builder
          everything discovery captured.
        </Empty>
      ) : (
        <div className="sw-infogrid">
          <Info label="STATUS" value={STATUS_LABEL[d.status] || d.status} />
          <Info label="BUILDER" value={d.owner_name || 'Unassigned'} />
          <Info label="TARGET" value={d.due_at ? dateTime(d.due_at) : 'Not set'} />
          <Info label="LATEST UPDATE" value={latest} />
        </div>
      )}

      <div className="sw-flex sw-mt" style={{ flexWrap: 'wrap' }}>
        {!requested && (
          <button className="sw-btn sw-primary" disabled={saving} onClick={onRequest}>
            Request demo
          </button>
        )}
        {d.url && (
          <a className="sw-btn sw-primary" href={d.url} target="_blank"
             rel="noopener noreferrer" style={{ textDecoration: 'none' }}>
            Open demo
          </a>
        )}
        <button className="sw-btn" onClick={() => setReqOpen(o => !o)}>
          {reqOpen ? 'Hide requirements' : 'Update requirements'}
        </button>
        {mayManage && requested && d.status !== 'ready' && d.status !== 'delivered' && (
          <button className="sw-btn" disabled={saving}
                  onClick={() => onPatch({ demo_status: 'ready' })}>
            Mark ready
          </button>
        )}
      </div>

      {reqOpen && (
        <div className="sw-field">
          <label>WHAT THIS DEMO HAS TO SHOW</label>
          <textarea className="sw-textarea" value={reqs}
                    placeholder="Prefilled from discovery when the demo was requested"
                    onChange={e => setReqs(e.target.value)} />
          <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
            <button className="sw-btn sw-primary" disabled={saving}
                    onClick={() => { onPatch({ demo_requirements: reqs }); setReqOpen(false) }}>
              {saving ? 'Saving…' : 'Save requirements'}
            </button>
          </div>
        </div>
      )}

      {/* ── the work order ────────────────────────────────────────────────
          Status, builder, target date, the environment URL and the build's
          own notes. This is how the demo gets MADE, not how it gets sold,
          and it is only here for the person making it. */}
      {mayManage && (
        <details className="sw-disclose">
          <summary>Internal demo details</summary>
          <div className="sw-grid-even">
            <div className="sw-field">
              <label>STATUS</label>
              <select className="sw-select" value={form.demo_status || ''}
                      onChange={e => setForm(f => ({ ...f, demo_status: e.target.value }))}>
                {DEMO_STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="sw-field">
              <label>BUILDER</label>
              <select className="sw-select" value={form.demo_owner_user_id || ''}
                      onChange={e => setForm(f => ({ ...f, demo_owner_user_id: e.target.value }))}>
                <option value="">Unassigned</option>
                {(team || []).map(t =>
                  <option key={t.id} value={t.id}>{t.full_name}</option>)}
              </select>
            </div>
            <div className="sw-field">
              <label>TARGET COMPLETION</label>
              <input className="sw-input" type="date" value={form.demo_due_at || ''}
                     onChange={e => setForm(f => ({ ...f, demo_due_at: e.target.value }))} />
            </div>
            <div className="sw-field">
              <label>DEMO URL</label>
              <input className="sw-input" value={form.demo_url || ''}
                     placeholder="set when the environment exists"
                     onChange={e => setForm(f => ({ ...f, demo_url: e.target.value }))} />
            </div>
          </div>
          <div className="sw-field">
            <label>INTERNAL BUILD NOTES</label>
            <textarea className="sw-textarea" value={form.demo_notes || ''}
                      onChange={e => setForm(f => ({ ...f, demo_notes: e.target.value }))} />
            <div className="sw-subtle" style={{ marginTop: 5 }}>
              Never customer-facing. No code path puts this in a proposal, an
              email or the deal room.
            </div>
          </div>
          <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
            <button className="sw-btn sw-primary" onClick={saveInternal} disabled={saving}>
              {saving ? 'Saving…' : 'Save demo build'}
            </button>
          </div>
        </details>
      )}
    </Card>
  )
}
