import { useState, useEffect, useCallback } from 'react'
import { api, getCurrentUser } from '../api/client'

const TC = { mobile: '#22c55e', landline: '#f59e0b', voip: '#8b5cf6', unknown: '#6b7280' }
const CH = { sms: { color: '#22c55e', label: 'SMS' }, email: { color: '#3b82f6', label: 'Email' }, voice: { color: '#f59e0b', label: 'Voice' } }

// Miles to meters conversion
const MILE_OPTIONS = [
  { miles: 1,   meters: 1609  },
  { miles: 3,   meters: 4828  },
  { miles: 5,   meters: 8047  },
  { miles: 10,  meters: 16093 },
  { miles: 15,  meters: 24140 },
  { miles: 25,  meters: 40234 },
  { miles: 50,  meters: 80467 },
]

const PRESETS = [
  { label: 'Funeral Homes',      query: 'funeral homes',          icon: '🏛' },
  { label: 'Insurance Agents',   query: 'insurance agency',       icon: '🛡' },
  { label: 'Financial Advisors', query: 'financial advisor',      icon: '📊' },
  { label: 'Real Estate',        query: 'real estate agent',      icon: '🏠' },
  { label: 'Car Dealerships',    query: 'car dealership',         icon: '🚗' },
  { label: 'Fiber / ISP',        query: 'internet service provider', icon: '📡' },
  { label: 'Law Firms',          query: 'law firm',               icon: '⚖' },
  { label: 'Accountants',        query: 'CPA accounting firm',    icon: '🧾' },
  { label: 'Chiropractors',      query: 'chiropractor',           icon: '🦴' },
  { label: 'Roofing Companies',  query: 'roofing contractor',     icon: '🏗' },
]

const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']

function Stars({ rating }) {
  if (!rating) return <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
  const full = Math.floor(rating)
  const half = rating % 1 >= 0.5
  return (
    <span title={`${rating}/5`} style={{ fontSize: 12, letterSpacing: 1 }}>
      {'★'.repeat(full)}{half ? '½' : ''}{'☆'.repeat(5 - full - (half ? 1 : 0))}
      <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 4 }}>{rating}</span>
    </span>
  )
}

function exportCSV(rows) {
  const cols = ['Name', 'Phone', 'Phone Type', 'Channel', 'Address', 'Website', 'Rating']
  const lines = [cols.join(','), ...rows.map(r => [
    `"${(r.name || '').replace(/"/g, '""')}"`,
    r.phone || '',
    r.phone_type || '',
    r.channel || '',
    `"${(r.address || '').replace(/"/g, '""')}"`,
    r.website || '',
    r.rating || '',
  ].join(','))]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = 'leads_export.csv'; a.click()
  URL.revokeObjectURL(url)
}

const IS = { padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-1)', color: 'var(--text)', fontSize: 14, boxSizing: 'border-box', width: '100%' }
const BTN = (color = '#3b82f6', extra = {}) => ({ padding: '8px 16px', borderRadius: 6, border: 'none', background: color, color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap', ...extra })

export default function LeadScraper() {
  const user = getCurrentUser()
  const [query, setQuery]       = useState('')
  const [city, setCity]         = useState('')
  const [state, setState]       = useState('')
  const [zip, setZip]           = useState('')
  const [radiusMi, setRadiusMi] = useState(5)   // displayed in miles; converted to meters on submit
  const [maxR, setMaxR]         = useState(20)
  const [searching, setSearching]   = useState(false)
  const [results, setResults]       = useState([])
  const [sel, setSel]               = useState(new Set())
  const [existing, setExisting]     = useState(new Set())
  const [validating, setValidating] = useState(false)
  const [importing, setImporting]   = useState(false)
  const [listName, setListName]     = useState('')
  const [importResult, setImportResult] = useState(null)
  const [err, setErr]               = useState('')
  const [searchMeta, setSearchMeta] = useState(null)

  // Org assignment
  const [orgs, setOrgs]               = useState([])
  const [targetOrgId, setTargetOrgId] = useState('')
  const [orgsLoading, setOrgsLoading] = useState(false)

  useEffect(() => {
    setOrgsLoading(true)
    api.get('/god/orgs?limit=200')
      .then(data => {
        const list = Array.isArray(data) ? data : (data?.orgs || [])
        setOrgs(list)
        if (list.length > 0) setTargetOrgId(String(list[0].id))
      })
      .catch(() => {})
      .finally(() => setOrgsLoading(false))
  }, [])

  function buildLocation() {
    const parts = [city.trim(), state.trim(), zip.trim()].filter(Boolean)
    return parts.join(', ')
  }

  async function handleSearch(e) {
    e.preventDefault()
    if (!query.trim()) return
    const loc = buildLocation()
    const radiusMeters = MILE_OPTIONS.find(o => o.miles === radiusMi)?.meters || 8047
    setSearching(true); setResults([]); setSel(new Set()); setExisting(new Set()); setImportResult(null); setErr('')
    try {
      const data = await api.post('/scraper/search', {
        query: query.trim(),
        location: loc || undefined,
        radius_meters: radiusMeters,
        max_results: maxR,
      })
      const res = data.results || []
      setResults(res)
      setSearchMeta({ query: data.query, total: data.total })
      if (!res.length) { setErr('No results found. Try a broader query or different location.'); return }

      const phones = res.map(r => r.phone).filter(Boolean)
      if (phones.length) {
        const ex = await api.post('/scraper/exists', { phones })
        setExisting(new Set(ex.existing_phones || []))
      }
    } catch (ex) { setErr(ex?.detail || 'Search failed. Check that GOOGLE_PLACES_API_KEY is set in Render.') }
    finally { setSearching(false) }
  }

  async function handleValidate() {
    const idxs = results.reduce((a, r, i) => { if (sel.has(i) && r.phone) a.push(i); return a }, [])
    if (!idxs.length) { setErr('No phone numbers in selection to validate.'); return }
    setValidating(true); setErr('')
    try {
      const phones = idxs.map(i => results[i].phone)
      const data = await api.post('/scraper/validate', { phones })
      const updated = [...results]
      idxs.forEach((ri, vi) => {
        const info = data.results?.[vi] || {}
        updated[ri] = { ...updated[ri], phone_type: info.phone_type, channel: info.phone_type === 'mobile' ? 'sms' : (updated[ri].website ? 'email' : 'voice'), validated: true }
      })
      setResults(updated)
    } catch (ex) { setErr(ex?.detail || 'Validation failed. Check Twilio credentials.') }
    finally { setValidating(false) }
  }

  async function handleImport() {
    if (!sel.size) { setErr('Select at least one result to import.'); return }
    if (!targetOrgId) { setErr('Select a target organization.'); return }
    const leads = results.filter((_, i) => sel.has(i))
    setImporting(true); setErr('')
    try {
      const data = await api.post('/scraper/import', {
        leads,
        list_name: listName || 'Lead Scraper Import',
        target_org_id: parseInt(targetOrgId, 10),
      })
      setImportResult(data)
      setSel(new Set())
    } catch (ex) { setErr(ex?.detail || 'Import failed.') }
    finally { setImporting(false) }
  }

  function toggle(i) { setSel(p => { const s = new Set(p); s.has(i) ? s.delete(i) : s.add(i); return s }) }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: '0 16px 40px' }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Lead Scraper</h1>
        <p style={{ color: 'var(--text-muted)', marginTop: 4, fontSize: 14 }}>
          Find local businesses via Google Places → validate phones → import to any org.
        </p>
      </div>

      {/* Quick presets */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Industry Presets</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {PRESETS.map(p => (
            <button key={p.label} onClick={() => setQuery(p.query)}
              style={{ fontSize: 12, padding: '5px 12px', borderRadius: 20, border: '1px solid var(--border)', background: query === p.query ? 'var(--accent,#3b82f6)' : 'var(--surface-2)', color: query === p.query ? '#fff' : 'var(--text)', cursor: 'pointer', fontWeight: 500 }}>
              {p.icon} {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Search form */}
      <form onSubmit={handleSearch} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: 20, marginBottom: 16, marginTop: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>Search</div>

        {/* Query */}
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Business Type / Search Query</label>
          <input style={IS} value={query} onChange={e => setQuery(e.target.value)} placeholder='e.g. "funeral homes" or "financial advisors near me"' required />
        </div>

        {/* Location row */}
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>Location</div>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 12, marginBottom: 14 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>City</label>
            <input style={IS} value={city} onChange={e => setCity(e.target.value)} placeholder="Dallas" />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>State</label>
            <select value={state} onChange={e => setState(e.target.value)} style={{ ...IS }}>
              <option value="">— State —</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>ZIP <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(optional)</span></label>
            <input style={IS} value={zip} onChange={e => setZip(e.target.value)} placeholder="75201" maxLength={10} />
          </div>
        </div>

        {/* Radius + max + search */}
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Radius</label>
            <select value={radiusMi} onChange={e => setRadiusMi(+e.target.value)} style={{ ...IS, width: 120 }}>
              {MILE_OPTIONS.map(({ miles }) => <option key={miles} value={miles}>{miles} {miles === 1 ? 'mile' : 'miles'}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Max Results</label>
            <select value={maxR} onChange={e => setMaxR(+e.target.value)} style={{ ...IS, width: 90 }}>
              {[10,20,30,40,60].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <button type="submit" disabled={searching || !query.trim()} style={{ ...BTN('#3b82f6', { opacity: searching || !query.trim() ? .6 : 1, padding: '8px 24px' }) }}>
            {searching ? '🔍 Searching...' : '🔍 Search'}
          </button>
          {city && state && (
            <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>
              📍 {[city, state, zip].filter(Boolean).join(', ')} · {radiusMi} mi radius
            </span>
          )}
        </div>
      </form>

      {err && (
        <div style={{ background: 'rgba(239,68,68,.1)', border: '1px solid rgba(239,68,68,.3)', borderRadius: 8, padding: '10px 16px', marginBottom: 16, color: '#ef4444', fontSize: 14 }}>
          {err}
        </div>
      )}

      {results.length > 0 && (
        <>
          {/* Summary bar */}
          <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              <strong style={{ color: 'var(--text)' }}>{results.length}</strong> found
              {existing.size > 0 && <> · <span style={{ color: '#f59e0b' }}>{existing.size} already in system</span></>}
              {sel.size > 0 && <> · <strong style={{ color: 'var(--text)' }}>{sel.size}</strong> selected</>}
            </div>
            <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', flexWrap: 'wrap' }}>
              {[
                ['All', () => setSel(new Set(results.map((_,i)=>i)))],
                ['None', () => setSel(new Set())],
                ['New Only', () => setSel(new Set(results.reduce((a,r,i)=>{ if (!existing.has(r.phone)) a.push(i); return a },[])))],
                ['SMS-Ready', () => setSel(new Set(results.reduce((a,r,i)=>{ if (r.phone_type==='mobile'||(!r.phone_type&&r.phone)) a.push(i); return a },[])))],
              ].map(([l, fn]) => (
                <button key={l} onClick={fn} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--surface-2)', color: 'var(--text)', cursor: 'pointer' }}>{l}</button>
              ))}
              <button onClick={handleValidate} disabled={validating || !sel.size}
                style={{ ...BTN('#8b5cf6', { opacity: validating || !sel.size ? .6 : 1, fontSize: 12, padding: '4px 12px' }) }}>
                {validating ? 'Validating...' : '📞 Validate Phones'}
              </button>
              <button onClick={() => exportCSV(sel.size ? results.filter((_,i)=>sel.has(i)) : results)}
                style={{ ...BTN('#6b7280', { fontSize: 12, padding: '4px 10px' }) }}>
                ↓ CSV
              </button>
            </div>
          </div>

          {/* Results table */}
          <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', marginBottom: 20 }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-3)', borderBottom: '1px solid var(--border)' }}>
                    <th style={{ width: 36, padding: '10px 12px' }}>
                      <input type="checkbox"
                        checked={sel.size === results.length && results.length > 0}
                        onChange={e => e.target.checked ? setSel(new Set(results.map((_,i)=>i))) : setSel(new Set())} />
                    </th>
                    {['Business','Phone','Type','Channel','Rating','Address / Website'].map(h => (
                      <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => {
                    const isDupe = existing.has(r.phone)
                    return (
                      <tr key={i} onClick={() => toggle(i)}
                        style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', background: isDupe ? 'rgba(245,158,11,.05)' : sel.has(i) ? 'rgba(59,130,246,.08)' : 'transparent', opacity: isDupe ? .75 : 1 }}>
                        <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                          <input type="checkbox" checked={sel.has(i)} onChange={() => toggle(i)} onClick={e => e.stopPropagation()} />
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          <div style={{ fontWeight: 600 }}>{r.name || '—'}</div>
                          {isDupe && <div style={{ fontSize: 10, color: '#f59e0b', fontWeight: 700, marginTop: 2 }}>✓ Already in system</div>}
                        </td>
                        <td style={{ padding: '10px 12px', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                          {r.phone || <span style={{ color: 'var(--text-muted)' }}>No phone</span>}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          {r.phone_type ? (
                            <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700, background: (TC[r.phone_type] || '#6b7280') + '22', color: TC[r.phone_type] || '#6b7280', border: `1px solid ${(TC[r.phone_type] || '#6b7280')}44` }}>
                              {r.phone_type}
                            </span>
                          ) : r.phone ? <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>unvalidated</span> : '—'}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          {r.channel ? <span style={{ fontSize: 12, fontWeight: 700, color: (CH[r.channel] || {}).color || 'var(--text)' }}>{(CH[r.channel] || {}).label || r.channel}</span> : '—'}
                        </td>
                        <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>
                          <Stars rating={r.rating} />
                          {r.reviews_count ? <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 4 }}>({r.reviews_count})</span> : null}
                        </td>
                        <td style={{ padding: '10px 12px', maxWidth: 260 }}>
                          <div style={{ color: 'var(--text-muted)', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.address || '—'}</div>
                          {r.website && (
                            <a href={r.website} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                              style={{ fontSize: 11, color: '#3b82f6', display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 240 }}>
                              {r.website.replace(/^https?:\/\//, '')}
                            </a>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Import panel */}
          <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: 20 }}>
            <h3 style={{ margin: '0 0 4px', fontSize: 15, fontWeight: 700 }}>Import to Leads</h3>
            <p style={{ margin: '0 0 16px', fontSize: 13, color: 'var(--text-muted)' }}>
              {sel.size} selected · duplicates skipped automatically
            </p>

            {importResult && (
              <div style={{ background: 'rgba(34,197,94,.1)', border: '1px solid rgba(34,197,94,.3)', borderRadius: 8, padding: '10px 16px', marginBottom: 14, fontSize: 14, color: '#22c55e' }}>
                ✅ Imported <strong>{importResult.imported}</strong> leads to <em>"{importResult.list_name}"</em>.
                {importResult.skipped > 0 && <> {importResult.skipped} duplicate{importResult.skipped !== 1 ? 's' : ''} skipped.</>}
                <a href="/leads" style={{ color: '#22c55e', marginLeft: 10, fontWeight: 600 }}>View Leads →</a>
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Target Organization</label>
                {orgsLoading ? (
                  <div style={{ ...IS, color: 'var(--text-muted)' }}>Loading orgs…</div>
                ) : (
                  <select value={targetOrgId} onChange={e => setTargetOrgId(e.target.value)} style={IS}>
                    <option value="">— Select org —</option>
                    {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                  </select>
                )}
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>List Name</label>
                <input value={listName} onChange={e => setListName(e.target.value)}
                  placeholder="e.g. Dallas Funeral Homes Aug 2026"
                  style={IS} />
              </div>
              <button onClick={handleImport} disabled={importing || !sel.size || !targetOrgId}
                style={{ ...BTN('#22c55e', { opacity: importing || !sel.size || !targetOrgId ? .6 : 1, padding: '8px 22px', alignSelf: 'flex-end' }) }}>
                {importing ? 'Importing...' : `Import ${sel.size} Lead${sel.size !== 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
