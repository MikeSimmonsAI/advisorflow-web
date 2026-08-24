import { useState, useCallback } from 'react'
import { api } from '../api/client'

const TC = { mobile: '#22c55e', landline: '#f59e0b', voip: '#8b5cf6', unknown: '#6b7280' }
const CH = { sms: { color: '#22c55e', label: 'SMS' }, email: { color: '#3b82f6', label: 'Email' }, voice: { color: '#f59e0b', label: 'Voice' } }

const PRESETS = [
  { label: 'Funeral Homes', query: 'funeral homes', icon: '🏛' },
  { label: 'Insurance Agents', query: 'insurance agency', icon: '🛡' },
  { label: 'Financial Advisors', query: 'financial advisor', icon: '📊' },
  { label: 'Law Firms', query: 'law firm', icon: '⚖' },
  { label: 'Real Estate', query: 'real estate agent', icon: '🏠' },
  { label: 'Accountants', query: 'CPA accounting firm', icon: '🧾' },
]

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
  const [query, setQuery] = useState('')
  const [loc, setLoc] = useState('')
  const [radius, setRadius] = useState(8000)
  const [maxR, setMaxR] = useState(20)
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState([])
  const [sel, setSel] = useState(new Set())
  const [existing, setExisting] = useState(new Set())   // phones already in system
  const [validating, setValidating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [listName, setListName] = useState('')
  const [importResult, setImportResult] = useState(null)
  const [err, setErr] = useState('')
  const [searchMeta, setSearchMeta] = useState(null)

  async function handleSearch(e) {
    e.preventDefault()
    if (!query.trim()) return
    setSearching(true); setResults([]); setSel(new Set()); setExisting(new Set()); setImportResult(null); setErr('')
    try {
      const data = await api.post('/scraper/search', {
        query: query.trim(),
        location: loc.trim() || undefined,
        radius_meters: radius,
        max_results: maxR,
      })
      const res = data.results || []
      setResults(res)
      setSearchMeta({ query: data.query, total: data.total })
      if (!res.length) { setErr('No results found. Try a broader query or location.'); return }

      // Check which phones already exist in the org
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
        updated[ri] = {
          ...updated[ri],
          phone_type: info.phone_type,
          channel: info.phone_type === 'mobile' ? 'sms' : (updated[ri].website ? 'email' : 'voice'),
          validated: true,
        }
      })
      setResults(updated)
    } catch (ex) { setErr(ex?.detail || 'Validation failed. Check Twilio credentials.') }
    finally { setValidating(false) }
  }

  async function handleImport() {
    if (!sel.size) { setErr('Select at least one result to import.'); return }
    const leads = results.filter((_, i) => sel.has(i))
    setImporting(true); setErr('')
    try {
      const data = await api.post('/scraper/import', { leads, list_name: listName || 'Lead Scraper Import' })
      setImportResult(data)
      setSel(new Set())
    } catch (ex) { setErr(ex?.detail || 'Import failed.') }
    finally { setImporting(false) }
  }

  function toggle(i) { setSel(p => { const s = new Set(p); s.has(i) ? s.delete(i) : s.add(i); return s }) }

  const newOnly = results.filter((r, i) => !existing.has(r.phone))
  const smsReady = results.filter(r => r.phone_type === 'mobile' || (!r.phone_type && r.phone))

  return (
    <div style={{ maxWidth: 1150, margin: '0 auto', padding: '0 16px 40px' }}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Lead Scraper</h1>
        <p style={{ color: 'var(--text-muted)', marginTop: 4, fontSize: 14 }}>
          Find local businesses via Google Places, validate phones, then import directly into your leads.
        </p>
      </div>

      {/* Quick presets */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
        {PRESETS.map(p => (
          <button key={p.label} onClick={() => setQuery(p.query)}
            style={{ fontSize: 12, padding: '5px 12px', borderRadius: 20, border: '1px solid var(--border)', background: query === p.query ? 'var(--accent,#3b82f6)' : 'var(--surface-2)', color: query === p.query ? '#fff' : 'var(--text)', cursor: 'pointer', fontWeight: 500 }}>
            {p.icon} {p.label}
          </button>
        ))}
      </div>

      {/* Search form */}
      <form onSubmit={handleSearch} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: 20, marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Search Query</label>
            <input style={IS} value={query} onChange={e => setQuery(e.target.value)} placeholder='e.g. "funeral homes" or "financial advisors"' required />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Location / City</label>
            <input style={IS} value={loc} onChange={e => setLoc(e.target.value)} placeholder="Dallas, TX" />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Radius (m)</label>
            <select value={radius} onChange={e => setRadius(+e.target.value)} style={{ ...IS, width: 130 }}>
              {[[1000,'1 km'],[3000,'3 km'],[5000,'5 km'],[8000,'8 km'],[15000,'15 km'],[25000,'25 km'],[40000,'40 km']].map(([v,l]) => <option key={v} value={v}>{l}</option>)}
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
              {existing.size > 0 && <> · <span style={{ color: '#f59e0b' }}>{existing.size} already in leads</span></>}
              {sel.size > 0 && <> · <strong style={{ color: 'var(--text)' }}>{sel.size}</strong> selected</>}
            </div>

            {/* Selection shortcuts */}
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
                          {isDupe && <div style={{ fontSize: 10, color: '#f59e0b', fontWeight: 700, marginTop: 2 }}>✓ Already in leads</div>}
                        </td>
                        <td style={{ padding: '10px 12px', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                          {r.phone || <span style={{ color: 'var(--text-muted)' }}>No phone</span>}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          {r.phone_type ? (
                            <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700, background: (TC[r.phone_type] || '#6b7280') + '22', color: TC[r.phone_type] || '#6b7280', border: `1px solid ${(TC[r.phone_type] || '#6b7280')}44` }}>
                              {r.phone_type}
                            </span>
                          ) : r.phone ? (
                            <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>unvalidated</span>
                          ) : '—'}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          {r.channel ? (
                            <span style={{ fontSize: 12, fontWeight: 700, color: (CH[r.channel] || {}).color || 'var(--text)' }}>
                              {(CH[r.channel] || {}).label || r.channel}
                            </span>
                          ) : '—'}
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
            <p style={{ margin: '0 0 14px', fontSize: 13, color: 'var(--text-muted)' }}>
              {sel.size} selected · duplicates are skipped automatically
            </p>

            {importResult && (
              <div style={{ background: 'rgba(34,197,94,.1)', border: '1px solid rgba(34,197,94,.3)', borderRadius: 8, padding: '10px 16px', marginBottom: 14, fontSize: 14, color: '#22c55e' }}>
                ✅ Imported <strong>{importResult.imported}</strong> leads to <em>"{importResult.list_name}"</em>.
                {importResult.skipped > 0 && <> {importResult.skipped} duplicate{importResult.skipped !== 1 ? 's' : ''} skipped.</>}
                <a href="/leads" style={{ color: '#22c55e', marginLeft: 10, fontWeight: 600 }}>View Leads →</a>
              </div>
            )}

            <div style={{ display: 'flex', gap: 10 }}>
              <input value={listName} onChange={e => setListName(e.target.value)}
                placeholder="List name (e.g. Dallas Funeral Homes Aug 2026)"
                style={{ ...IS, flex: 1 }} />
              <button onClick={handleImport} disabled={importing || !sel.size}
                style={{ ...BTN('#22c55e', { opacity: importing || !sel.size ? .6 : 1, padding: '8px 22px' }) }}>
                {importing ? 'Importing...' : `Import ${sel.size} Lead${sel.size !== 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
