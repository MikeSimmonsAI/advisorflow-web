/**
 * The Proposal panel on an Opportunity — create, edit, price, publish, send,
 * and watch what the buyer does with it.
 *
 * BUILT FOR A SALESPERSON, NOT A DEVELOPER. Everything AdvisorFlow already
 * knows is prefilled by the server, so this is a page for editing prose and
 * pressing Send — not a form for re-entering the company name.
 *
 * PRICING AUTHORITY IS SERVER-SIDE. The discount field is hidden from a rep
 * because `can_override_price` says so, but hiding it is a courtesy, not the
 * control — the API refuses a rep's discount regardless of what this renders.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import { Card, Chip, Empty, ErrorBar, dateTime } from './parts'

const STATUS_TONE = {
  draft: null, internal_review: null, ready: 'blue', sent: 'blue',
  viewed: 'amber', accepted: 'green', declined: 'red',
  change_requested: 'amber', expired: 'red', superseded: null,
}

const SECTIONS = [
  ['executive_summary', 'OVERVIEW', 'A short framing the customer reads first'],
  ['business_need', 'THE SITUATION TODAY', 'What you heard in discovery'],
  ['objectives', 'OBJECTIVES', 'What they want to be true afterwards'],
  ['recommended_solution', 'RECOMMENDED SOLUTION', 'What you are proposing'],
  ['scope', 'SCOPE', "What's included"],
  ['deliverables', 'DELIVERABLES', 'What they actually receive'],
  ['implementation_plan', 'IMPLEMENTATION', 'How it gets done'],
  ['terms', 'TERMS', 'Commercial terms'],
]

function money(v, cur) {
  if (v === null || v === undefined) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency: cur || 'USD', maximumFractionDigits: 0,
    }).format(v)
  } catch { return (cur || 'USD') + ' ' + v }
}


export default function ProposalPanel({ opp, packages = [], onChanged }) {
  const [data, setData] = useState(null)
  const [current, setCurrent] = useState(null)
  const [draft, setDraft] = useState({})
  const [activity, setActivity] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [reason, setReason] = useState('')
  const [resource, setResource] = useState({ block_type: 'website_url', content: '', file_url: '' })

  const load = useCallback(async () => {
    setError(null)
    try {
      const r = await api.get('/sales/opportunities/' + opp.id + '/proposals')
      setData(r)
      const cur = (r.proposals || []).find(p => p.id === r.current_id) || null
      if (cur) {
        const full = await api.get('/sales/proposals/' + cur.id)
        setCurrent(full)
        setDraft(SECTIONS.reduce((acc, [k]) => ({ ...acc, [k]: full[k] || '' }), {}))
        try {
          setActivity(await api.get('/sales/proposals/' + cur.id + '/activity'))
        } catch { setActivity(null) }
      } else {
        setCurrent(null); setActivity(null)
      }
    } catch (e) { setError(e.message || 'Could not load proposals.') }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  async function act(fn, okMsg) {
    setBusy(true); setError(null); setNote(null)
    try {
      await fn()
      if (okMsg) setNote(okMsg)
      await load()
      if (onChanged) await onChanged()
    } catch (e) { setError(e.message || 'That did not work.') }
    finally { setBusy(false) }
  }

  const create = () => act(
    () => api.post('/sales/proposals', { opportunity_id: opp.id }),
    'Proposal created and prefilled from this opportunity.')

  const saveSections = () => act(
    () => api.patch('/sales/proposals/' + current.id, draft),
    'Saved.')

  const publish = () => act(
    () => api.post('/sales/proposals/' + current.id + '/publish', {}),
    'Published to the deal room. Nothing has been sent yet.')

  const send = () => act(
    () => api.post('/sales/proposals/' + current.id + '/send', {}),
    'Sent. The customer now has a secure link.')

  const newVersion = () => act(
    () => api.post('/sales/proposals/' + current.id + '/version', {}),
    'Version created. The previous one is kept as superseded.')

  const revoke = () => act(
    () => api.post('/sales/proposals/' + current.id + '/revoke-access', {}),
    'Every live link for this proposal has been revoked.')

  const setPackage = pid => act(
    () => api.patch('/sales/proposals/' + current.id, { package_id: pid }))

  const applyDiscount = adj => act(
    () => api.patch('/sales/proposals/' + current.id,
                    { adjustment: Number(adj), price_reason: reason }),
    'Pricing updated.')

  const addResource = () => act(async () => {
    await api.post('/sales/proposals/' + current.id + '/blocks', resource)
    setResource({ block_type: 'website_url', content: '', file_url: '' })
  }, 'Added to the deal room.')

  const removeBlock = id => act(
    () => api.delete('/sales/proposals/' + current.id + '/blocks/' + id))


  if (!data) {
    return <Card title="PROPOSAL"><div className="sw-subtle">Loading…</div></Card>
  }

  if (!current) {
    return (
      <Card title="PROPOSAL"
            sub="Built from this opportunity — you should not have to retype anything"
            right={<button className="sw-btn sw-primary" onClick={create} disabled={busy}>
              {busy ? 'Creating…' : 'Create proposal'}</button>}>
        <ErrorBar error={error} />
        <Empty title="No proposal yet">
          <b>Create proposal</b> pulls in the company, the contact, the selected
          package and its price, and turns the discovery answers into a first
          draft. You edit the wording, not the data.
        </Empty>
      </Card>
    )
  }

  const p = current
  const history = (data.proposals || []).filter(x => x.id !== p.id)
  const sent = !!p.sent_at

  return (
    <Card
      title={'PROPOSAL ' + (p.proposal_number || '')}
      sub={'Version ' + p.version + (p.editable ? ' · editable' : ' · locked, create a version to change it')}
      right={<Chip tone={STATUS_TONE[p.status]}>{p.status_label}</Chip>}>
      <ErrorBar error={error} />
      {note && <div className="sw-subtle" style={{ color: '#047857', marginBottom: 10 }}>{note}</div>}

      {/* ── pricing ─────────────────────────────────────────────────────── */}
      <div className="sw-field">
        <label>PACKAGE</label>
        <select className="sw-select" value={p.package_id || ''} disabled={!p.editable || busy}
                onChange={e => setPackage(e.target.value)}>
          <option value="">— choose a package —</option>
          {packages.map(pk => (
            <option key={pk.id} value={pk.id}>
              {pk.name}{pk.price != null ? ' — ' + money(pk.price, pk.currency) : ' — custom'}
            </option>
          ))}
        </select>
      </div>

      <div className="sw-flex sw-between" style={{ padding: '10px 0' }}>
        <span className="sw-subtle">List price</span>
        <b>{money(p.base_amount, p.currency)}</b>
      </div>
      {p.adjustment ? (
        <div className="sw-flex sw-between" style={{ padding: '4px 0' }}>
          <span className="sw-subtle">Adjustment</span>
          <b style={{ color: '#9e6722' }}>{money(p.adjustment, p.currency)}</b>
        </div>
      ) : null}
      <div className="sw-flex sw-between"
           style={{ padding: '10px 0', borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 12 }}>Customer pays</b>
        <b style={{ fontSize: 16 }}>{money(p.final_amount, p.currency)}</b>
      </div>

      {/* Manager-only. The server refuses a rep's discount either way — this
          just avoids showing a control that would always fail. */}
      {p.can_override_price && p.editable && (
        <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                      borderRadius: 8, padding: 12, marginTop: 10 }}>
          <div className="sw-subtle" style={{ marginBottom: 8 }}>
            Manager pricing adjustment — a reason is required and is recorded.
          </div>
          <input className="sw-input" placeholder="Reason (e.g. competitive vs Vendor X)"
                 value={reason} onChange={e => setReason(e.target.value)} />
          <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
            <input className="sw-input" type="number" placeholder="-500"
                   style={{ width: 130 }}
                   onKeyDown={e => { if (e.key === 'Enter') applyDiscount(e.target.value) }}
                   id="adj-input" />
            <button className="sw-tiny" disabled={busy || !reason.trim()}
                    onClick={() => applyDiscount(document.getElementById('adj-input').value)}>
              Apply adjustment
            </button>
          </div>
        </div>
      )}
      {p.price_override_reason && (
        <div className="sw-subtle" style={{ marginTop: 8 }}>
          Adjusted {dateTime(p.price_override_at)} — {p.price_override_reason}
        </div>
      )}


      {/* ── content ─────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
        {SECTIONS.map(([key, label, hint]) => (
          <div className="sw-field" key={key}>
            <label>{label}</label>
            <textarea className="sw-input" rows={key === 'executive_summary' ? 3 : 4}
                      value={draft[key] || ''} disabled={!p.editable}
                      placeholder={hint}
                      onChange={e => setDraft({ ...draft, [key]: e.target.value })} />
          </div>
        ))}
        {p.editable && (
          <div className="sw-flex" style={{ justifyContent: 'flex-end' }}>
            <button className="sw-btn" onClick={saveSections} disabled={busy}>
              {busy ? 'Saving…' : 'Save wording'}
            </button>
          </div>
        )}
      </div>

      {/* ── deal room content ───────────────────────────────────────────── */}
      <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 11 }}>DEAL ROOM CONTENT</b>
        <div className="sw-subtle" style={{ margin: '4px 0 10px' }}>
          The proposal text above is generated automatically. Anything you add
          here — a demo, a deck, a document — sits alongside it and is never
          overwritten when you republish.
        </div>
        {(p.blocks || []).filter(b => !b.generated).map(b => (
          <div key={b.id} className="sw-flex sw-between" style={{ padding: '6px 0' }}>
            <div style={{ minWidth: 0 }}>
              <b style={{ fontSize: 11 }}>{b.content || b.block_type}</b>
              <div className="sw-subtle" style={{ overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.file_url}</div>
            </div>
            <button className="sw-tiny" disabled={busy}
                    onClick={() => removeBlock(b.id)}>Remove</button>
          </div>
        ))}
        <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
          <select className="sw-select" style={{ width: 150 }} value={resource.block_type}
                  onChange={e => setResource({ ...resource, block_type: e.target.value })}>
            <option value="website_url">Demo / website</option>
            <option value="pdf">Document</option>
            <option value="video">Video</option>
            <option value="cta">Link</option>
          </select>
          <input className="sw-input" placeholder="Label" value={resource.content}
                 onChange={e => setResource({ ...resource, content: e.target.value })} />
          <input className="sw-input" placeholder="https://…" value={resource.file_url}
                 onChange={e => setResource({ ...resource, file_url: e.target.value })} />
          <button className="sw-tiny" disabled={busy || !resource.file_url.trim()}
                  onClick={addResource}>Add</button>
        </div>
        {opp.demo_url && !(p.blocks || []).some(b => b.file_url === opp.demo_url) && (
          <button className="sw-tiny" style={{ marginTop: 8 }} disabled={busy}
                  onClick={() => { setResource({ block_type: 'website_url',
                    content: 'Your demo', file_url: opp.demo_url }) }}>
            Use this deal's demo: {opp.demo_url}
          </button>
        )}
      </div>

      {/* ── actions ─────────────────────────────────────────────────────── */}
      <div className="sw-flex" style={{ gap: 8, marginTop: 18, paddingTop: 14,
                                        borderTop: '1px solid #eef2f5', flexWrap: 'wrap' }}>
        <button className="sw-btn" onClick={publish} disabled={busy}>
          {p.is_published ? 'Republish' : 'Publish'}
        </button>
        <button className="sw-btn sw-primary" onClick={send} disabled={busy}>
          {sent ? 'Re-send to customer' : 'Send to customer'}
        </button>
        {sent && (
          <button className="sw-btn" onClick={newVersion} disabled={busy}>
            New version
          </button>
        )}
        {sent && (
          <button className="sw-tiny" onClick={revoke} disabled={busy}>
            Revoke access
          </button>
        )}
      </div>
      <div className="sw-subtle" style={{ marginTop: 8 }}>
        Publishing puts it in the deal room and sends nothing. Sending emails the
        customer a private link.
      </div>


      {/* ── buyer activity ──────────────────────────────────────────────── */}
      {activity && (
        <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
          <b style={{ fontSize: 11 }}>BUYER ACTIVITY</b>
          {p.customer_response_note && (
            <div style={{ background: '#fff6e9', border: '1px solid #f2d5aa',
                          borderRadius: 8, padding: 10, margin: '8px 0' }}>
              <b style={{ fontSize: 11 }}>They said:</b>
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                “{p.customer_response_note}”
              </div>
            </div>
          )}
          {activity.events.length === 0 ? (
            <div className="sw-subtle" style={{ marginTop: 6 }}>
              {sent ? 'Sent, but they have not opened it yet.'
                    : 'Nothing yet — this has not been sent.'}
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              {activity.events.slice(0, 12).map(e => (
                <div key={e.id} className="sw-flex sw-between" style={{ padding: '5px 0' }}>
                  <span style={{ fontSize: 11 }}>
                    {e.label}{e.detail ? ' — ' + e.detail : ''}
                    {e.proposal_version && e.proposal_version !== p.version
                      ? ' (v' + e.proposal_version + ')' : ''}
                  </span>
                  <span className="sw-subtle">{dateTime(e.occurred_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── version history ─────────────────────────────────────────────── */}
      {history.length > 0 && (
        <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid #eef2f5' }}>
          <b style={{ fontSize: 11 }}>EARLIER VERSIONS</b>
          <div className="sw-subtle" style={{ margin: '4px 0 8px' }}>
            Kept exactly as they were sent. Nothing is overwritten.
          </div>
          {history.map(h => (
            <div key={h.id} className="sw-flex sw-between" style={{ padding: '5px 0' }}>
              <span style={{ fontSize: 11 }}>
                v{h.version} · {money(h.final_amount, h.currency)}
              </span>
              <Chip tone={STATUS_TONE[h.status]}>{h.status_label}</Chip>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
