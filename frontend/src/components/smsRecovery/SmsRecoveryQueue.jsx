/* SMS RECOVERY QUEUE (#482) - platform-shared, tenant-scoped.

   One row per family whose text failed (or whose texts are held) in the
   period. "Held" families get no automated text until a person records
   evidence and releases them - and a release never sends at once: the next
   text keeps its normal day and passes every other gate (STOP/DNC, consent,
   contact hours, campaign switch). A carrier failure here is never DNC and
   never proof of a landline. Nothing on this screen sends anything, and
   there is no bulk action.

   Reads GET /sms-recovery/queue; acts through POST
   /sms-recovery/items/{id}/action (managers only, audited). Any lane can
   mount it: it needs only the shared UI primitives and the api client. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fetchObjectUrl } from '../../api/client'
import {
  Banner, Chip, Drawer, Empty, ErrorLine, Field, FilterChips, Icon, KV, Kpi, Loading, Notice, PageHead, Panel,
  errText, when,
} from '../../pages/program/sci/ui'

const KIND = {
  held: ['Texts held', 'bad'], investigate: ['Investigate', 'warn'], watch: ['Schedule continues', 'info'],
  resolved: ['Resolved', 'ok'],
}
const STATE = {
  sms_review_required: 'Held - needs review', sms_temporary_hold: 'Temporary hold',
  sms_verified_unreachable: 'Verified: cannot take texts', sms_active: 'Texts allowed',
}
const REVIEW = {
  open: 'Not reviewed', review_requested: 'Review requested', verified_capable: 'Verified: can take texts',
  verified_not_capable: 'Verified: cannot take texts',
}
const OUTCOME = {
  released: 'Released after review', delivered_after_failure: 'Twilio later reported delivered',
  temporary_hold_expired: 'Temporary hold ended', opted_out: 'Opted out (STOP/DNC)',
}

const nice = s => (s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

export default function SmsRecoveryQueue({ title = 'SMS Recovery Queue', openPerson }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [f, setF] = useState({ kind: 'held', code: '', campaign: '', location_id: '', owner_id: '', fallback: '', outcome: '', date_from: '', date_to: '' })
  const [open, setOpen] = useState(null)
  const [msg, setMsg] = useState('')

  const query = useMemo(() => {
    const p = new URLSearchParams()
    Object.entries(f).forEach(([k, v]) => { if (v && !(k === 'kind' && v === 'all')) p.set(k, v) })
    return p.toString()
  }, [f])

  const load = useCallback(() => {
    api.get(`/sms-recovery/queue${query ? `?${query}` : ''}`).then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e)))
  }, [query])
  useEffect(() => { load() }, [load])

  const set = (k, v) => setF(s => ({ ...s, [k]: v }))
  if (!d) return <><PageHead title={title} />{err ? <ErrorLine text={err} /> : <Loading label="Loading the queue…" />}</>

  const m = d.metrics
  const o = d.options
  const exportEvidence = async () => {
    try {
      const url = await fetchObjectUrl(`/sms-recovery/evidence.csv${f.code ? `?code=${f.code}` : ''}`)
      const a = document.createElement('a')
      a.href = url
      a.download = 'sms-delivery-evidence.csv'
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) { setErr(errText(e)) }
  }

  return (
    <>
      <PageHead title={title} sub="Texts the carrier could not deliver, and what happens next. A failure here is never treated as opt-out (DNC) or as proof of a landline.">
        <button type="button" className="sci-btn" onClick={load}><Icon name="refresh" />Refresh</button>
        {d.can_act && <button type="button" className="sci-btn" onClick={exportEvidence} title="Full message IDs for a Twilio support ticket. Phone numbers stay masked.">
          <Icon name="doc" />Export evidence</button>}
      </PageHead>
      <ErrorLine text={err} />
      <Notice text={msg} onClose={() => setMsg('')} />

      <section className="sci-kpis four" aria-label="Delivery recovery summary">
        <Kpi icon="users" tone="red" value={m.families_affected} label="Families with a failed text" note={`since ${when(m.since)}`} />
        <Kpi icon="pause" tone="gold" value={m.holds_open} label="Texts on hold" note={`${m.holds_resolved} resolved`}
          onClick={() => set('kind', 'held')} />
        <Kpi icon="ban" tone="blue" value={m.repeat_attempts_prevented} label="Repeat texts prevented" note="scheduled texts held back" />
        <Kpi icon="mail" tone="green" value={m.email_fallback_used} label="Reached by email instead" note="held families emailed since" />
      </section>

      <Panel title="Texts in this period" labelledBy="smsr-totals">
        <KV rows={[
          ['Attempted', m.attempted],
          ['Accepted by the provider', m.accepted_by_provider],
          ['Delivered (carrier confirmed)', m.delivered],
          ['Not delivered', m.failed],
          ['No final result yet', m.pending],
          ['Failed by code', Object.entries(m.failed_by_code).map(([c, n]) => `${c}: ${n}`).join(' · ') || '—'],
          ['Outcomes', Object.entries(m.outcomes).map(([k, n]) => `${OUTCOME[k] || nice(k)}: ${n}`).join(' · ') || '—'],
        ]} />
        {(m.bottlenecks.waiting_review_over_a_day > 0 || m.bottlenecks.held_with_no_email_fallback > 0 || m.bottlenecks.verified_ready_to_release > 0) && (
          <Banner tone="warn" icon="alert" title="Waiting on a person">
            {m.bottlenecks.waiting_review_over_a_day} held over a day without review · {m.bottlenecks.held_with_no_email_fallback} held
            with no email to fall back on · {m.bottlenecks.verified_ready_to_release} verified and ready to release.
          </Banner>
        )}
      </Panel>

      <Panel title="Families" labelledBy="smsr-rows" pad
        aside={<FilterChips label="Show" value={f.kind || 'all'} onChange={v => set('kind', v)}
          items={[['held', 'Held'], ['investigate', 'Investigate'], ['watch', 'Continuing'], ['resolved', 'Resolved'], ['all', 'All']]} />}>
        <div className="smsr-filters" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <select className="sci-input sm" aria-label="Error code" value={f.code} onChange={e => set('code', e.target.value)}>
            <option value="">All error codes</option>
            {o.codes.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          {o.campaigns.length > 0 && <select className="sci-input sm" aria-label="Campaign" value={f.campaign} onChange={e => set('campaign', e.target.value)}>
            <option value="">All campaigns</option>
            {o.campaigns.map(c => <option key={c} value={c}>{nice(c)}</option>)}
          </select>}
          {o.locations.length > 0 && <select className="sci-input sm" aria-label="Location" value={f.location_id} onChange={e => set('location_id', e.target.value)}>
            <option value="">All locations</option>
            {o.locations.map(l => <option key={l.id} value={l.id}>{l.name || 'Unnamed'}</option>)}
          </select>}
          {o.owners.length > 0 && <select className="sci-input sm" aria-label="Owner" value={f.owner_id} onChange={e => set('owner_id', e.target.value)}>
            <option value="">All owners</option>
            {o.owners.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>}
          <select className="sci-input sm" aria-label="Email fallback" value={f.fallback} onChange={e => set('fallback', e.target.value)}>
            <option value="">Email: any</option>
            <option value="eligible">Email can carry on</option>
            <option value="none">No email to fall back on</option>
          </select>
          {o.outcomes.length > 0 && <select className="sci-input sm" aria-label="Outcome" value={f.outcome} onChange={e => set('outcome', e.target.value)}>
            <option value="">All outcomes</option>
            {o.outcomes.map(k => <option key={k} value={k}>{OUTCOME[k] || nice(k)}</option>)}
          </select>}
          <input type="date" className="sci-input sm" aria-label="From" value={f.date_from} onChange={e => set('date_from', e.target.value)} />
          <input type="date" className="sci-input sm" aria-label="To" value={f.date_to} onChange={e => set('date_to', e.target.value)} />
          <span className="sci-micro sci-muted" style={{ alignSelf: 'center' }}>Carrier names aren't in Twilio's receipts, so there is no carrier filter yet.</span>
        </div>

        {d.rows.length === 0 ? <Empty icon="check" title="Nothing here">No family matches these filters.</Empty> : (
          <div className="sci-tablewrap scroll">
            <table className="sci-table compact">
              <thead><tr><th>Family</th><th>Campaign · location</th><th>Latest error</th><th>Texts</th><th>Phone type</th><th>Email fallback</th><th>Status</th></tr></thead>
              <tbody>
                {d.rows.map(r => (
                  <tr key={r.lead_id} style={{ cursor: 'pointer' }} onClick={() => setOpen(r)}>
                    <td className="sci-cell-main">{r.name || r.name_masked}<div className="sci-micro sci-muted">{r.phone || 'no number'}</div></td>
                    <td className="sci-small">{nice(r.campaign) || '—'}<div className="sci-micro sci-muted">{r.location || ''}</div></td>
                    <td className="sci-small"><b>{r.latest_error.code || '—'}</b> {r.latest_error.label}
                      <div className="sci-micro sci-muted">{when(r.latest_error.at)} · {r.latest_error.provider_status}</div></td>
                    <td className="sci-small">{r.attempts} sent · {r.delivered} delivered · {r.failed} failed</td>
                    <td className="sci-small">{r.phone_type}</td>
                    <td className="sci-small">{r.fallback.eligible ? <Chip tone="ok">Email continues</Chip> : <span title={r.fallback.reason}>{r.fallback.reason}</span>}</td>
                    <td>
                      <Chip tone={KIND[r.kind]?.[1]}>{KIND[r.kind]?.[0] || r.kind}</Chip>
                      {r.hold && <div className="sci-micro sci-muted">{r.hold.disposition ? (OUTCOME[r.hold.disposition] || nice(r.hold.disposition)) : (REVIEW[r.hold.review_status] || r.hold.review_status)}</div>}
                      {r.opted_out && <div className="sci-micro">Opted out</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="How each error is handled" labelledBy="smsr-policy" aside={<span className="sci-micro sci-muted">Policy {d.policy.version}</span>}>
        <KV rows={Object.entries(d.policy.rules).map(([c, r]) => [c, `${r.label} - ${nice(r.action)}. ${r.note || ''}`])} />
      </Panel>

      <FamilyDrawer row={open} canAct={d.can_act} onClose={() => setOpen(null)} openPerson={openPerson}
        onDone={text => { setMsg(text); setOpen(null); load() }} />
    </>
  )
}

function FamilyDrawer({ row, canAct, onClose, onDone, openPerson }) {
  const [h, setH] = useState(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => {
    setH(null); setNote(''); setErr('')
    if (row) api.get(`/sms-recovery/families/${row.lead_id}/history`).then(setH).catch(e => setErr(errText(e)))
  }, [row])
  if (!row) return null
  const hold = row.hold && !row.hold.disposition ? row.hold : null

  const act = (action, text) => {
    setBusy(action); setErr('')
    api.post(`/sms-recovery/items/${row.item_id}/action`, { action, note })
      .then(res => onDone(action === 'release' && res.result?.next_text_not_before
        ? `${text} The next text is not before ${when(res.result.next_text_not_before)} and keeps its normal day.` : text))
      .catch(e => setErr(errText(e))).finally(() => setBusy(''))
  }

  return (
    <Drawer open={!!row} title={row.name || row.name_masked} sub={`${row.phone || 'no number'} · ${row.phone_type}`} onClose={onClose} width={560}
      footer={openPerson ? <button type="button" className="sci-btn" onClick={() => openPerson(row.lead_id)}><Icon name="user" />Open family</button> : null}>
      <ErrorLine text={err} />
      {hold && (
        <Banner tone={hold.state === 'sms_verified_unreachable' ? 'bad' : 'warn'} icon="pause" title={STATE[hold.state] || hold.state}>
          No automated text goes to this number while it is held. This is not opt-out (DNC), and email keeps its own schedule.
          {' '}Held {when(hold.opened_at)} under policy {hold.policy}; {hold.prevented} scheduled text{hold.prevented === 1 ? '' : 's'} held back so far.
        </Banner>
      )}
      <KV rows={[
        ['Latest error', `${row.latest_error.code || '—'} - ${row.latest_error.label || ''}`],
        ['What happens', row.latest_error.note || '—'],
        ['Message ID (tail)', row.latest_error.sid || '—'],
        ['Email', row.fallback.eligible ? `Can carry on (${row.fallback.emails_sent} sent, ${row.fallback.remaining ?? '—'} left)` : row.fallback.reason],
        ['Review', hold ? (REVIEW[hold.review_status] || hold.review_status) : (row.hold ? OUTCOME[row.hold.disposition] || nice(row.hold.disposition) : 'No hold')],
        row.hold?.evidence ? ['Evidence', row.hold.evidence] : null,
        h ? ['Texts from the family', h.texts_from_family] : null,
      ]} />

      {canAct && hold && (
        <Panel title="Review" labelledBy="smsr-review">
          <Field label="Note or evidence" hint="Required to verify: what shows this phone can or cannot take texts (the family confirmed by phone, a line check, ...)." full>
            <textarea rows={3} value={note} onChange={e => setNote(e.target.value)} disabled={!!busy} />
          </Field>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
            <button type="button" className="sci-btn sm" disabled={!!busy} onClick={() => act('request_review', 'Review requested.')}>Request number review</button>
            <button type="button" className="sci-btn sm" disabled={!!busy || !note.trim()} onClick={() => act('mark_capable', 'Recorded: this phone can take texts. It can be released now.')}>Verified: can take texts</button>
            <button type="button" className="sci-btn sm" disabled={!!busy || !note.trim()} onClick={() => act('mark_not_capable', 'Recorded: this phone cannot take texts. Texts stay stopped; email is unaffected.')}>Verified: cannot take texts</button>
            <button type="button" className="sci-btn sm" disabled={!!busy || !row.fallback.eligible} title={row.fallback.eligible ? '' : row.fallback.reason}
              onClick={() => act('route_email', 'Email will carry on on its own schedule. Email 1 is never sent twice.')}>Continue by email</button>
            <button type="button" className="sci-btn sm primary" disabled={!!busy || hold.review_status !== 'verified_capable'}
              title={hold.review_status === 'verified_capable' ? 'Lets the next scheduled text go on its normal day - nothing is sent now.' : 'Verify the phone first.'}
              onClick={() => act('release', 'Hold released.')}>Release hold</button>
          </div>
          <p className="sci-micro sci-muted" style={{ marginTop: 6 }}>Releasing never sends anything now and never resends the failed text.</p>
        </Panel>
      )}

      <Panel title="History" labelledBy="smsr-history">
        {!h ? <Loading /> : (
          <>
            <table className="sci-table compact">
              <thead><tr><th>Text sent</th><th>Result</th><th>Error</th><th>ID</th></tr></thead>
              <tbody>{h.messages.map((x, i) => (
                <tr key={i}><td className="sci-small">{when(x.at)}</td><td className="sci-small">{x.status || '—'}</td>
                  <td className="sci-small">{x.error_code || ''}</td><td className="sci-micro">{x.sid || ''}</td></tr>))}</tbody>
            </table>
            {h.events.length > 0 && (
              <ul className="sci-micro" style={{ marginTop: 8, paddingLeft: 18 }}>
                {h.events.map((e, i) => <li key={i}>{when(e.at)} - {nice(e.kind)}{e.error_code ? ` (${e.error_code})` : ''}</li>)}
              </ul>
            )}
          </>
        )}
      </Panel>
    </Drawer>
  )
}
