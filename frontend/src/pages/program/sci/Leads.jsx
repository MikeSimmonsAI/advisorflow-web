/* Leads: every family in the program, searchable, one row per person.
   Clicking a row opens the family's record inside the Family Service Center
   (Person.jsx). Reads GET /program/send/contacts - the same one-row-per-person
   list Choose & Send uses - so the two screens can never disagree. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, FilterChips, Loading, PageHead, Panel, SearchBox, errText, num, when } from './ui'

const PAGE = 50
const SHOW = [['all', 'Everyone'], ['approved', 'In a campaign'], ['not_approved', 'Not started']]
const STATUS_LABEL = {
  new: 'New', sent: 'Contacted', replied: 'Replied', hot: 'HOT', booked: 'Booked', dnc: 'Do not contact',
  completed: 'Completed', lost: 'Closed',
}

export default function Leads({ locations, locationId, openPerson, initialQuery = '' }) {
  const [search, setSearch] = useState(initialQuery)
  const [q, setQ] = useState(initialQuery)
  const [show, setShow] = useState('all')
  const [loc, setLoc] = useState(locationId || '')
  const [page, setPage] = useState(0)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [importing, setImporting] = useState(false)
  const [reload, setReload] = useState(0)
  const homes = useMemo(() => (locations || []).filter(l => !l.is_review_bucket), [locations])

  useEffect(() => { setLoc(locationId || '') }, [locationId])
  useEffect(() => { setSearch(initialQuery); setQ(initialQuery) }, [initialQuery])
  useEffect(() => { const t = setTimeout(() => setQ(search.trim()), 300); return () => clearTimeout(t) }, [search])
  useEffect(() => { setPage(0) }, [q, show, loc])

  useEffect(() => {
    let alive = true
    setData(null); setErr('')
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE), show })
    if (q) p.set('search', q)
    if (loc) p.set('location_id', loc)
    api.get(`/program/send/contacts?${p}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(errText(e)); setData({ items: [], total: 0 }) } })
    return () => { alive = false }
  }, [q, show, loc, page, reload])

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1

  return (
    <>
      <PageHead title="Leads" sub="Every family in the program. Open one to update their record, add notes or keep files.">
        <button type="button" className="sci-btn primary" onClick={() => setImporting(v => !v)}>{importing ? 'Close import' : 'Import leads'}</button>
      </PageHead>
      {importing && <ImportLeads onLoaded={() => setReload(n => n + 1)} />}
      <ErrorLine text={err} />
      <Panel title="Families" labelledBy="sci-leads"
        aside={<span className="sci-micro sci-muted">{data ? `${num(data.total)} found` : 'Loading…'}</span>}>
        <div className="sci-toolbar">
          <div className="sci-grow"><SearchBox id="sci-leads-search" value={search} onChange={setSearch} placeholder="Name, phone, email or Lead ID" label="Search families" width="100%" /></div>
          <label className="sci-sr" htmlFor="sci-leads-loc">Location</label>
          <select id="sci-leads-loc" className="sci-input sm" value={loc} onChange={e => setLoc(e.target.value)}>
            <option value="">All locations</option>
            {homes.map(l => <option key={l.location_id} value={l.location_id}>{l.name}</option>)}
          </select>
        </div>
        <div style={{ marginBottom: 10 }}><FilterChips items={SHOW} value={show} onChange={setShow} label="Show" /></div>
        {!data ? <Loading /> : !data.items.length ? (
          <Empty icon="users" title="No families here">Nothing matches this search.</Empty>
        ) : (
          <div className="sci-tablewrap scroll">
            <table className="sci-table compact">
              <thead><tr><th>Family</th><th>Location</th><th>Reaches by</th><th>Status</th><th><span className="sci-sr">Open</span></th></tr></thead>
              <tbody>
                {data.items.map(r => (
                  <tr key={r.lead_id} style={{ cursor: 'pointer' }} onClick={() => openPerson(r.lead_id)}>
                    <td className="sci-cell-main">
                      <button type="button" className="sci-namebtn" onClick={e => { e.stopPropagation(); openPerson(r.lead_id) }}>{r.name}</button>
                      <small>{[r.phone, r.email].filter(Boolean).join(' · ') || 'No contact details'} · Lead {r.source_lead_id}</small>
                    </td>
                    <td className="sci-small">{r.location || <Chip tone="warn" plain>No location</Chip>}<span className="sci-micro sci-muted" style={{ display: 'block' }}>{r.campaign || ''}</span></td>
                    <td className="sci-small">{r.reaches}
                      <span className="sci-micro sci-muted" style={{ display: 'block' }}>
                        {r.last_text_at ? `Last text ${when(r.last_text_at)}` : ''}{r.last_text_at && r.last_email_at ? ' · ' : ''}{r.last_email_at ? `Last email ${when(r.last_email_at)}` : ''}
                        {!r.last_text_at && !r.last_email_at ? (r.starts_at ? `Starts ${when(r.starts_at)}` : 'Nothing sent yet') : ''}
                      </span>
                    </td>
                    <td>
                      <Chip tone={r.status === 'hot' ? 'bad' : r.status === 'booked' ? 'ok' : ''} plain>{STATUS_LABEL[r.status] || r.status || '—'}</Chip>
                      {r.approved && <span className="sci-micro sci-muted" style={{ display: 'block' }}>In a campaign</span>}
                    </td>
                    <td style={{ textAlign: 'right' }}><button type="button" className="sci-btn sm" onClick={e => { e.stopPropagation(); openPerson(r.lead_id) }}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && pages > 1 && (
          <div className="sci-row-gap" style={{ justifyContent: 'space-between', marginTop: 10 }}>
            <button type="button" className="sci-btn sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</button>
            <span className="sci-micro sci-muted">Page {page + 1} of {pages}</span>
            <button type="button" className="sci-btn sm" disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)}>Next</button>
          </div>
        )}
      </Panel>
    </>
  )
}


/* IMPORT LEADS: pick a file, check it (changes nothing), then load it live.
   Loading puts clean contacts in the list, holds anyone who needs a look
   (unknown location, possible duplicate, not interested), checks every new
   phone for mobile vs landline, and SENDS NOTHING - families are only
   contacted after they are picked on Choose & Send. */
function ImportLeads({ onLoaded }) {
  const [file, setFile] = useState(null)
  const [check, setCheck] = useState(null)
  const [done, setDone] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const input = useRef(null)
  const send = async apply => {
    if (!file) { setErr('Choose a file first.'); return }
    setErr(''); setBusy(apply ? 'Loading…' : 'Checking…')
    const fd = new FormData(); fd.append('file', file); fd.append('apply', apply ? 'true' : 'false')
    try {
      const r = await api.upload('/program/leads/import', fd)
      if (apply) { setDone(r); setCheck(null); onLoaded() } else { setCheck(r); setDone(null) }
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }
  const st = check?.stage || {}
  const refused = st.refused || (st.errors && st.errors.length ? 'Some rows have a blank or repeated Lead ID.' : '')
  return (
    <Panel title="Import leads" labelledBy="sci-import">
      <p className="sci-micro sci-muted" style={{ marginTop: 0 }}>
        Excel (.xlsx) or CSV. Columns: Lead ID, First Name, Last Name, Phone, Email, Location Friendly Name, Primary Campaign, Lead Status (plus any others you have).
        Step 1 checks the file and changes nothing. Step 2 loads it. Loading never sends anything.
      </p>
      <div className="sci-row-gap">
        <input ref={input} className="sci-input" type="file" accept=".csv,.xlsx,.xls"
          onChange={e => { setFile(e.target.files?.[0] || null); setCheck(null); setDone(null); setErr('') }} />
        <button type="button" className="sci-btn" disabled={!file || !!busy} onClick={() => send(false)}>{busy === 'Checking…' ? busy : '1. Check file'}</button>
        <button type="button" className="sci-btn primary" disabled={!check || !!refused || !!busy} onClick={() => send(true)}>{busy === 'Loading…' ? busy : '2. Load leads'}</button>
      </div>
      <ErrorLine text={err} />
      {check && (
        <div className="sci-card" style={{ marginTop: 12 }}>
          <strong>{num(st.source_rows || 0)} rows · {num(st.contact_masters || 0)} people</strong>
          <div className="sci-rows" style={{ marginTop: 8 }}>
            <div className="sci-kv"><span>With a phone</span><span>{num(st.with_phone || 0)}</span></div>
            <div className="sci-kv"><span>With an email</span><span>{num(st.with_email || 0)}</span></div>
            <div className="sci-kv"><span>Matched to a location</span><span>{num(st.location_mapped || 0)}</span></div>
            <div className="sci-kv"><span>Location not recognized (held for review)</span><span>{num(st.location_review || 0)}</span></div>
            <div className="sci-kv"><span>Possible duplicates (held for review)</span><span>{num(st.duplicate_review_rows || 0)}</span></div>
            <div className="sci-kv"><span>Phone numbers that won't work</span><span>{num(st.bad_phone_rows || 0)}</span></div>
            <div className="sci-kv"><span>No phone (email only)</span><span>{num(st.no_phone_rows || 0)}</span></div>
          </div>
          {refused && <p role="alert" style={{ color: 'var(--bad)', marginBottom: 0 }}>Can't load this file: {refused}</p>}
          {!refused && <p className="sci-micro sci-muted" style={{ marginBottom: 0 }}>Looks good. Press "2. Load leads" to put them in the list.</p>}
        </div>
      )}
      {done && (
        <div className="sci-card" style={{ marginTop: 12, borderColor: 'var(--ok)' }}>
          <strong>Loaded. {num(done.promotion?.created || 0)} new families added.</strong>
          <p className="sci-micro sci-muted" style={{ margin: '6px 0 0' }}>
            {num(done.held?.held || 0)} held for review · {num(done.promotion?.already_live || 0)} were already in the list
            {done.line_check && !done.line_check.error ? ` · phones checked for mobile vs landline` : ''}.
            Nothing was sent. Pick who to reach on Choose & Send.
          </p>
        </div>
      )}
    </Panel>
  )
}
