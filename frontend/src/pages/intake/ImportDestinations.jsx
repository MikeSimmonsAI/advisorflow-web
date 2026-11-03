// #470 Stage B - Step 0 of every import: say where the records go.
// The list comes from the server (/intake/destinations) and holds only places
// this person may write to. Nothing is pre-selected from the last customer open.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setOrgContext } from '../../api/client'

const err = e => (e && (e.detail || e.message)) || 'Something went wrong'

export default function ImportDestinations() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [fail, setFail] = useState(null)
  const [kind, setKind] = useState('')
  const [target, setTarget] = useState('')

  useEffect(() => { api.get('/intake/destinations').then(setData).catch(e => setFail(err(e))) }, [])
  const d = data?.items?.find(x => x.key === kind)
  const t = d?.targets?.find(x => x.id === target)

  const go = () => {
    if (!d || !t) return
    if (d.key === 'tenant_contacts') {
      // A platform owner enters the chosen customer; the importer then shows
      // that customer and the server refuses the upload if it differs.
      if (data.is_platform_owner) setOrgContext(t.id, t.name)
      nav('/imports/new')
    } else if (d.key === 'evosys_sales') nav(`/imports/sales?target=${encodeURIComponent(t.id)}`)
    else if (d.key === 'customer_acquisition') nav(`/imports/acquisition?target=${encodeURIComponent(t.id)}`)
    else nav(d.path)
  }

  return (
    <div className="aq-page">
      <h1 className="aq-h1">Import records</h1>
      <p className="aq-lede">First choose where these records go. Importing never sends a message or starts a campaign.</p>
      {fail && <div className="aq-err">{fail}</div>}
      {!data && !fail && <p>Loading…</p>}
      {data && data.items.length === 0 && <p>You don't have a place to import into.</p>}
      {data && (
        <section className="aq-card">
          <h2>1. What are you importing?</h2>
          {data.items.map(x => (
            <label key={x.key} style={{ display: 'block', margin: '8px 0' }}>
              <input type="radio" name="kind" checked={kind === x.key}
                     onChange={() => { setKind(x.key); setTarget('') }} />{' '}
              <strong>{x.label}</strong> - {x.description}
            </label>
          ))}
        </section>
      )}
      {d && (
        <section className="aq-card">
          <h2>2. Which one?</h2>
          <select value={target} onChange={e => setTarget(e.target.value)}>
            <option value="">Choose…</option>
            {d.targets.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </section>
      )}
      {t && (
        <section className="aq-card">
          <p>Records will go to <strong>{d.label}</strong>: <strong>{t.name}</strong>.</p>
          <button className="btn btn-primary" onClick={go}>Continue</button>
        </section>
      )}
    </div>
  )
}
