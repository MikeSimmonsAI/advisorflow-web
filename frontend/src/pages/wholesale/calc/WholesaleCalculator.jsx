/* Wholesale > Deal Calculator. A blank calculator for any house (on a call,
 * at a walkthrough), or pre-filled from an EvoSense property with
 * ?property=<id>. Nothing is saved. */
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../../api/client'
import { EvoApp, PageHead } from '../ds/ds'
import '../ds/evo-pages.css'
import DealCalculator from './DealCalculator'
import { prefillFromProperty } from './prefill'

export default function WholesaleCalculator() {
  const [sp] = useSearchParams()
  const propertyId = sp.get('property')
  const [pre, setPre] = useState(propertyId ? null : {})
  const [err, setErr] = useState(null)
  useEffect(() => {
    if (!propertyId) return
    api.get(`/wholesale/evosense/properties/${propertyId}`)
      .then((d) => setPre(prefillFromProperty(d)))
      .catch(() => { setErr('That property could not be loaded - the calculator starts blank.'); setPre({}) })
  }, [propertyId])
  return (
    <EvoApp world="operations">
      <PageHead world="operations" eyebrow="Wholesale" title="Deal Calculator"
                sub={pre && pre.address ? `For ${pre.address}` : 'Repairs, costs, your fee - and the most you can offer the seller.'}
                actions={propertyId ? <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/evosense/property/${propertyId}`}>Back to property</Link> : null} />
      {err ? <p className="evo-muted evo-small">{err}</p> : null}
      {pre ? <DealCalculator key={propertyId || 'blank'} prefill={pre} /> : <p className="evo-muted">Loading the property…</p>}
    </EvoApp>
  )
}
