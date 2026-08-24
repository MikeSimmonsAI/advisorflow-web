import { useState } from 'react'
import { api } from '../api/client'

const TC = { mobile: '#22c55e', landline: '#f59e0b', voip: '#8b5cf6', unknown: '#6b7280' }

export default function LeadScraper() {
  const [query, setQuery] = useState('')
  const [loc, setLoc] = useState('')
  const [radius, setRadius] = useState(5000)
  const [maxR, setMaxR] = useState(20)
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState([])
  const [sel, setSel] = useState(new Set())
  const [validating, setValidating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [listName, setListName] = useState('')
  const [importResult, setImportResult] = useState(null)
  const [err, setErr] = useState('')

  async function handleSearch(e) {
    e.preventDefault()
    if (!query.trim()) return
    setSearching(true); setResults([]); setSel(new Set()); setImportResult(null); setErr('')
    try {
      const data = await api.post('/scraper/search', { query, location: loc, radius_meters: radius, max_results: maxR })
      setResults(data.results || [])
      if (!data.results?.length) setErr('No results found.')
    } catch (ex) { setErr(ex?.detail || 'Search failed') }
    finally { setSearching(false) }
  }

  async function handleValidate() {
    const idxs = results.reduce((a, r, i) => { if (sel.has(i) && r.phone) a.push(i); return a }, [])
    if (!idxs.length) { setErr('No phones in selection.'); return }
    setValidating(true); setErr('')
    try {
      const phones = idxs.map(i => results[i].phone)
      const data = await api.post('/scraper/validate', { phones })
      const updated = [...results]
      idxs.forEach((ri, vi) => {
        const info = data.results?.[vi] || {}
        updated[ri] = { ...updated[ri], phone_type: info.phone_type, channel: info.channel, validated: true }
      })
      setResults(updated)
    } catch (ex) { setErr(ex?.detail || 'Validation failed') }
    finally { setValidating(false) }
  }

  async function handleImport() {
    if (!sel.size) { setErr('Select leads first.'); return }
    const leads = results.filter((_, i) => sel.has(i))
    setImporting(true); setErr('')
    try {
      const data = await api.post('/scraper/import', { leads, list_name: listName || 'Lead Scraper Import' })
      setImportResult(data); setSel(new Set())
    } catch (ex) { setErr(ex?.detail || 'Import failed') }
    finally { setImporting(false) }
  }

  function toggle(i) { setSel(p => { const s = new Set(p); s.has(i) ? s.delete(i) : s.add(i); return s }) }
  const IS = { padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-1)', color: 'var(--text)', fontSize: 14, boxSizing: 'border-box', width: '100%' }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 16px' }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>Lead Scraper</h1>
      <p style={{ color: 'var(--text-muted)', marginBottom: 20 }}>Search Google Places, validate phones, and import leads.</p>
      {err && <div style={{ background: 'rgba(239,68,68,.1)', border: '1px solid rgba(239,68,68,.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 16, color: '#ef4444', fontSize: 14 }}>{err}</div>}
      <form onSubmit={handleSearch} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: 20, marginBottom: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div><label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Query</label><input style={IS} value={query} onChange={e => setQuery(e.target.value)} placeholder="funeral homes Dallas TX" /></div>
          <div><label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Location</label><input style={IS} value={loc} onChange={e => setLoc(e.target.value)} placeholder="Dallas, TX" /></div>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <div><label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Radius (m)</label><input type="number" value={radius} onChange={e => setRadius(+e.target.value)} min={500} max={50000} step={500} style={{ ...IS, width: 110 }} /></div>
          <div><label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', display: 'block', marginBottom: 4 }}>Max</label><input type="number" value={maxR} onChange={e => setMaxR(+e.target.value)} min={1} max={60} style={{ ...IS, width: 80 }} /></div>
          <button type="submit" disabled={searching || !query.trim()} style={{ padding: '8px 22px', borderRadius: 6, border: 'none', background: '#3b82f6', color: '#fff', fontWeight: 600, cursor: 'pointer', opacity: searching || !query.trim() ? .6 : 1 }}>{searching ? 'Searching...' : 'Search'}</button>
        </div>
      </form>
      {results.length > 0 && (<>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{results.length} results - {sel.size} selected</span>
          <button onClick={() => setSel(new Set(results.map((_,i)=>i)))} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 5, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text)', cursor: 'pointer' }}>All</button>
          <button onClick={() => setSel(new Set())} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 5, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text)', cursor: 'pointer' }}>None</button>
          <button onClick={() => setSel(new Set(results.reduce((a,r,i)=>{ if (!r.phone_type||r.phone_type==='mobile') a.push(i); return a },[])))} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 5, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text)', cursor: 'pointer' }}>SMS-Ready</button>
          <button onClick={handleValidate} disabled={validating||!sel.size} style={{ fontSize: 12, padding: '3px 12px', borderRadius: 5, border: 'none', background: '#8b5cf6', color: '#fff', fontWeight: 600, cursor: 'pointer', marginLeft: 'auto' }}>{validating ? 'Validating...' : 'Validate Phones'}</button>
        </div>
        <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', marginBottom: 20 }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ background: 'var(--surface-3)', borderBottom: '1px solid var(--border)' }}>
                <th style={{ width: 36, padding: '10px 12px' }}></th>
                {['Name','Phone','Type','Channel','Address'].map(h=><th key={h} style={{ padding:'10px 12px', textAlign:'left', fontWeight:600 }}>{h}</th>)}
              </tr></thead>
              <tbody>{results.map((r,i)=>(
                <tr key={i} onClick={()=>toggle(i)} style={{ borderBottom:'1px solid var(--border)', cursor:'pointer', background: sel.has(i)?'rgba(59,130,246,.08)':'transparent' }}>
                  <td style={{ padding:'10px 12px', textAlign:'center' }}><input type="checkbox" checked={sel.has(i)} onChange={()=>toggle(i)} onClick={e=>e.stopPropagation()} /></td>
                  <td style={{ padding:'10px 12px', fontWeight:500 }}>{r.name||'-'}</td>
                  <td style={{ padding:'10px 12px', fontVariantNumeric:'tabular-nums' }}>{r.phone||'-'}</td>
                  <td style={{ padding:'10px 12px' }}>{r.phone_type?<span style={{ padding:'2px 8px', borderRadius:4, fontSize:11, fontWeight:700, background:(TC[r.phone_type]||'#6b7280')+'22', color:TC[r.phone_type]||'#6b7280', border:`1px solid ${(TC[r.phone_type]||'#6b7280')}44` }}>{r.phone_type}</span>:r.phone?<span style={{ color:'var(--text-muted)', fontSize:11 }}>unvalidated</span>:'-'}</td>
                  <td style={{ padding:'10px 12px' }}>{r.channel?<span style={{ fontSize:12, fontWeight:700, color:r.channel==='sms'?'#22c55e':'#3b82f6' }}>{r.channel.toUpperCase()}</span>:'-'}</td>
                  <td style={{ padding:'10px 12px', color:'var(--text-muted)', maxWidth:200, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{r.address||'-'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
        <div style={{ background:'var(--surface-2)', border:'1px solid var(--border)', borderRadius:10, padding:20 }}>
          <h3 style={{ margin:'0 0 12px', fontSize:15, fontWeight:700 }}>Import Selected to Leads</h3>
          {importResult&&<div style={{ background:'rgba(34,197,94,.1)', border:'1px solid rgba(34,197,94,.3)', borderRadius:8, padding:'10px 14px', marginBottom:12, fontSize:14, color:'#22c55e' }}>Imported {importResult.imported}. {importResult.skipped} duplicates skipped.</div>}
          <div style={{ display:'flex', gap:10 }}>
            <input value={listName} onChange={e=>setListName(e.target.value)} placeholder="List name (optional)" style={{ flex:1, padding:'8px 12px', borderRadius:6, border:'1px solid var(--border)', background:'var(--surface-1)', color:'var(--text)', fontSize:14 }} />
            <button onClick={handleImport} disabled={importing||!sel.size} style={{ padding:'8px 20px', borderRadius:6, border:'none', background:'#22c55e', color:'#fff', fontWeight:700, cursor:'pointer', whiteSpace:'nowrap' }}>{importing?'Importing...':`Import ${sel.size} Lead${sel.size!==1?'s':''}`}</button>
          </div>
        </div>
      </>)}
    </div>
  )
}