/* PROPERTY TYPE, ITS DETAILS AND ITS ANALYSIS — on the property page.
 *
 * The type comes from the county's own category code (or a person). The fields
 * shown follow the type; each says where it came from, and a missing one says
 * "Not on record". The analysis is the type's own method (income approach for
 * commercial / multifamily / industrial, price per acre for land / agricultural);
 * residential uses the existing Deal Analysis. Nothing is filled in. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Panel, Tag, money } from '../ds/ds'
import { CATEGORIES, subtypesOf } from './ptypes'

function show(f) {
  if (f.value === null || f.value === undefined || f.value === '') return <span className="evo-muted">Not on record</span>
  if (f.kind === 'money') return money(f.value)
  if (f.kind === 'pct') return `${f.value}%`
  return String(f.value)
}

export default function PropertyTypePanel({ propertyId, canEdit }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [editType, setEditType] = useState(false)
  const [cat, setCat] = useState('')
  const [sub, setSub] = useState('')
  const [edit, setEdit] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    api.get(`/wholesale/evosense/properties/${propertyId}/type-details`).then(setD).catch((e) => setErr(errText(e)))
  }, [propertyId])
  useEffect(() => { load() }, [load])
  if (!d) return err ? <Alert kind="warn">{err}</Alert> : null
  const pc = d.property_class
  const a = d.analysis

  async function saveType() {
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/evosense/properties/${propertyId}/property-type`, { category: cat, subtype: sub || null }); setEditType(false); load() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function saveFacts() {
    setBusy(true); setErr('')
    try {
      const values = {}; const evidence = {}
      Object.entries(edit).forEach(([k, v]) => { values[k] = v.value === '' ? null : v.value; if (v.evidence) evidence[k] = v.evidence })
      setD(await api.post(`/wholesale/evosense/properties/${propertyId}/type-facts`, { values, evidence })); setEdit(null)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const fields = [...d.section, ...d.deal]

  return (
    <Panel title="Property type & details" id="ptype"
           hint={pc.needs_review ? 'Property type needs review' : `${pc.confidence_label}${pc.code ? ` · code ${pc.code}` : ''}`}>
      <p style={{ margin: '0 0 8px' }}>
        <b>{pc.needs_review ? 'Property type needs review' : pc.category_label}</b>
        {pc.subtype_label ? ` · ${pc.subtype_label}` : ''}
        {pc.source ? <span className="evo-muted evo-small"> — per {pc.source}</span> : null}
        {canEdit ? <button className="evo-btn evo-btn--ghost evo-btn--sm" style={{ marginLeft: 8 }}
                           onClick={() => { setEditType(!editType); setCat(pc.category === 'unknown' ? '' : pc.category); setSub(pc.subtype || '') }}>
          {editType ? 'Cancel' : 'Set type'}</button> : null}
      </p>
      {pc.conflict ? <Alert kind="warn">{pc.conflict}</Alert> : null}
      {editType ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          <select className="evo-select" style={{ width: 'auto' }} value={cat} onChange={(e) => { setCat(e.target.value); setSub('') }}>
            <option value="">Choose a type…</option>
            {CATEGORIES.filter(([k]) => k !== 'unknown').map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {cat && subtypesOf(cat).length ? (
            <select className="evo-select" style={{ width: 'auto' }} value={sub} onChange={(e) => setSub(e.target.value)}>
              <option value="">Any subtype</option>
              {subtypesOf(cat).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>) : null}
          <button className="evo-btn evo-btn--primary evo-btn--sm" disabled={!cat || busy} onClick={saveType}>Save type</button>
          <span className="evo-small evo-muted">The county's code stays on record beside your entry.</span>
        </div>
      ) : null}

      <table className="evo-table evo-small">
        <tbody>
          {fields.map((f) => (
            <tr key={f.key}>
              <th scope="row" style={{ textAlign: 'left', fontWeight: 600 }}>{f.label}</th>
              <td>{edit ? (
                <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <input className="evo-input" style={{ maxWidth: 180 }} value={edit[f.key]?.value ?? (f.source.startsWith('Entered') ? f.value ?? '' : '')}
                         placeholder={f.value != null ? String(f.value) : ''} inputMode={f.kind === 'text' ? undefined : 'decimal'}
                         onChange={(e) => setEdit({ ...edit, [f.key]: { ...(edit[f.key] || {}), value: e.target.value } })} />
                  <input className="evo-input" style={{ maxWidth: 220 }} placeholder="Evidence (rent roll, survey, MLS #)"
                         value={edit[f.key]?.evidence ?? ''}
                         onChange={(e) => setEdit({ ...edit, [f.key]: { ...(edit[f.key] || { value: f.source.startsWith('Entered') ? f.value : '' }), evidence: e.target.value } })} />
                </span>
              ) : show(f)}</td>
              <td className="evo-muted">{f.source}{f.evidence ? ` · ${f.evidence}` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit ? (
        <div style={{ display: 'flex', gap: 8, margin: '8px 0' }}>
          {edit ? <>
            <button className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={saveFacts}>Save facts</button>
            <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setEdit(null)}>Cancel</button></>
            : <button className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => setEdit({})}>Enter facts</button>}
        </div>
      ) : null}

      <h3 className="evo-small" style={{ margin: '12px 0 4px', textTransform: 'uppercase', letterSpacing: '.06em' }}>Analysis</h3>
      <p className="evo-small" style={{ margin: '0 0 6px' }}>{a.method}</p>
      {a.delegate === 'deal_analysis' ? (
        <p className="evo-small">Residential properties use Deal Analysis (ARV from closed sales, repairs, MAO) — see <a href="#deal">Deal intelligence</a>.</p>
      ) : (
        <>
          <Tag kind={a.stage === 'supported' ? 'live' : a.stage === 'preliminary' ? 'estimate' : 'danger'}>{a.stage_label}</Tag>
          {a.lines.length ? (
            <ul className="evo-fcc-list" style={{ marginTop: 8 }}>
              {a.lines.map((l) => (
                <li key={l.label}><span><b>{l.label}</b> <span className="evo-muted evo-small">= {l.how}</span>
                  <span className="evo-muted evo-small" style={{ display: 'block' }}>
                    {l.inputs.map((i) => `${i.label}: ${i.value} (${i.source})`).join(' · ')}</span></span>
                  <b>{money(l.value)}</b></li>))}
            </ul>
          ) : null}
          {a.tax_value ? <p className="evo-small evo-muted">{a.tax_value.label}: {money(a.tax_value.value)}</p> : null}
          {a.missing.length ? <p className="evo-small"><b>Needs financial information:</b> {a.missing.join(', ')}</p> : null}
        </>
      )}
      {d.questions.length ? (
        <>
          <h3 className="evo-small" style={{ margin: '12px 0 4px', textTransform: 'uppercase', letterSpacing: '.06em' }}>Questions for the owner</h3>
          <ul className="evo-small" style={{ margin: 0, paddingLeft: 18 }}>{d.questions.map((q) => <li key={q}>{q}</li>)}</ul>
        </>
      ) : null}
      {d.closing_checklist?.length ? (
        <>
          <h3 className="evo-small" style={{ margin: '12px 0 4px', textTransform: 'uppercase', letterSpacing: '.06em' }}>Closing checklist</h3>
          {d.contract_note ? <Alert kind="warn">{d.contract_note}</Alert> : null}
          <ul className="evo-small" style={{ margin: 0, paddingLeft: 18 }}>{d.closing_checklist.map((q) => <li key={q}>{q}</li>)}</ul>
        </>
      ) : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
    </Panel>
  )
}
