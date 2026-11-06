/* CLEAN-UP & BATCH 1 (Issue #507). Read-only view of the recorded data
   clean-up and the first campaign's audience snapshot:
   - every count is a server count from the recorded run, and each tile opens
     the exact families behind it (paged, with the reason);
   - the campaign is shown as it is: a draft, its sender, its steps, why it
     can't be switched on yet. Nothing on this screen sends, approves or
     switches anything on. */
import { useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Kpi, Loading, PageHead, Panel, errText, num } from './ui'

const TILES = [
  ['batch_1', 'Batch 1 (staged)', 'send', 'blue'],
  ['ready', 'Ready (all eligible)', 'check', 'green'],
  ['needs_review', 'Needs classification', 'review', 'orange'],
  ['contact_data', 'Contact data to fix', 'alert', 'orange'],
  ['duplicate_review', 'Duplicate review', 'users', 'orange'],
  ['suppressed', 'Do not contact', 'pause', 'red'],
  ['prior_conversation', 'Replied before', 'responses', 'blue'],
  ['past_appointment', 'Past appointments', 'calendar', 'blue'],
  ['earlier_sequence', 'In earlier sequence', 'clock', ''],
  ['previously_contacted', 'Contacted before', 'mail', ''],
  ['shared_number', 'Shared number (held)', 'users', ''],
  ['email_only', 'Email only', 'mail', ''],
  ['merged_duplicate', 'Duplicate rows (linked)', 'users', ''],
  ['test', 'Test records', 'pause', ''],
]
const COUNT_OF = (s, k) => {
  const seg = s?.audience_segments || {}, disp = s?.dispositions || {}, q = s?.review_queues || {}
  return { batch_1: s?.batch_1, ready: (seg.ready || 0), needs_review: q.needs_classification, contact_data: q.contact_data,
    duplicate_review: q.duplicate, suppressed: disp.suppressed, prior_conversation: seg.prior_conversation,
    past_appointment: seg.past_appointment, earlier_sequence: seg.earlier_sequence, previously_contacted: seg.previously_contacted,
    shared_number: seg.shared_number, email_only: disp.email_only, merged_duplicate: disp.merged_duplicate, test: disp.test }[k] || 0
}
const fmt = iso => (iso ? new Date(iso).toLocaleString([], { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : '—')
const PAGE = 25

function List({ bucket, label, openPerson }) {
  const [page, setPage] = useState(0)
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const seq = useRef(0)
  useEffect(() => { setPage(0) }, [bucket])
  useEffect(() => {
    const n = ++seq.current
    setErr(''); setD(null)
    api.get(`/program/command/data-review/list?bucket=${bucket}&offset=${page * PAGE}&limit=${PAGE}`)
      .then(r => { if (n === seq.current) setD(r) }).catch(e => { if (n === seq.current) setErr(errText(e)) })
  }, [bucket, page])
  if (err) return <ErrorLine text={`Could not load this list: ${err}`} />
  if (!d) return <Loading />
  if (!d.total) return <Empty title="Nobody in this list" />
  const to = Math.min(d.total, (page + 1) * PAGE)
  return <>
    <div className="rl-pager"><span>{label}: {num(page * PAGE + 1)}–{num(to)} of <b>{num(d.total)}</b></span>
      <span><button disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Previous</button>{' '}
        <button disabled={to >= d.total} onClick={() => setPage(page + 1)}>Next ›</button></span></div>
    <div className="sci-tablewrap"><table className="sci-table">
      <thead><tr><th>Family</th><th>Number</th><th>Status</th><th>Why</th><th>Last contact</th></tr></thead>
      <tbody>{d.items.map(i => <tr key={i.lead_id}>
        <td><button className="sci-link" onClick={() => openPerson(i.lead_id)}>{i.name}</button>
          {i.replied_before ? <> <Chip tone="info">replied before</Chip></> : null}</td>
        <td>{i.phone || (i.phone_issue ? <Chip tone="warn">{i.phone_issue}</Chip> : '—')}{i.shares_phone_with ? <small> · shared ×{i.shares_phone_with + 1}</small> : null}</td>
        <td>{(i.status || '').replace(/_/g, ' ')}{i.tier ? <small> · {i.tier.replace(/_/g, ' ')}</small> : null}</td>
        <td><small>{(i.reasons || []).join('; ') || (i.disposition || '').replace(/_/g, ' ')}{i.source_file ? ` · from ${i.source_file}` : ''}</small></td>
        <td><small>{fmt(i.last_contact_at)}</small></td>
      </tr>)}</tbody></table></div>
  </>
}

export default function DataReview({ openPerson }) {
  const [s, setS] = useState(null)
  const [err, setErr] = useState('')
  const [bucket, setBucket] = useState('batch_1')
  useEffect(() => {
    api.get('/program/command/data-review/summary').then(setS).catch(e => setErr(errText(e)))
  }, [])
  if (err) return <><PageHead title="Clean-up & Batch 1" /><ErrorLine text={err} /></>
  if (!s) return <Loading />
  const c = s.campaign
  const label = (TILES.find(t => t[0] === bucket) || [])[1]
  return <>
    <PageHead title="Clean-up & Batch 1" sub="The recorded data clean-up and the first campaign, as a draft. Nothing here sends.">
      {s.run ? <Chip tone="ok">Clean-up applied {fmt(s.run.finished_at)}</Chip> : <Chip tone="warn">Clean-up not applied yet</Chip>}
    </PageHead>
    {!s.run && <Empty title="No clean-up has been recorded for this workspace yet" />}
    {c && <Panel title={c.name} aside={<Chip tone={c.is_active ? 'ok' : 'warn'}>{c.is_active ? 'On' : 'Draft · off'}</Chip>}>
      <dl className="rl-kv">
        <dt>Goal</dt><dd>A 20–30 minute in-person Family File Review with Mike Simmons at Restland, 13005 Greenville Ave, Dallas, TX 75243</dd>
        <dt>Sender</dt><dd>{c.sender} (shared toll-free line)</dd>
        <dt>Time zone</dt><dd>{c.timezone}</dd>
        <dt>Booking</dt><dd>{c.booking_user_set ? 'Booking calendar set' : 'Booking calendar not set yet — texts carry no booking link until it is'}</dd>
        <dt>Switch-on check</dt><dd>{c.activation_blocker || 'Every step has wording. Switching on is a separate, deliberate step in Campaigns.'}</dd>
      </dl>
      <h4 style={{ margin: '14px 0 6px' }}>Texts</h4>
      {c.steps.map(t => <p key={t.touch} className="small"><b>Text {t.touch}</b> · day {t.day}, {t.hour > 12 ? `${t.hour - 12} PM` : `${t.hour} AM`} Central<br />{t.text}</p>)}
    </Panel>}
    <div className="sci-kpis" style={{ marginTop: 14 }}>
      {TILES.map(([k, l, icon, tone]) => <Kpi key={k} icon={icon} tone={tone} value={COUNT_OF(s, k)} label={l}
        note={bucket === k ? 'Showing below' : 'Open list'} onClick={() => setBucket(k)} />)}
    </div>
    <Panel title={label}><List bucket={bucket} label={label} openPerson={openPerson} /></Panel>
    <p className="cmd-muted small">Counts overlap across lists: a family can be both "Contacted before" and "Duplicate review". Families stays the complete database.</p>
  </>
}
