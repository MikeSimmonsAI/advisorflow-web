import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

/**
 * GETTING STARTED — on My Day for a new salesperson.
 *
 * The real first action, then the sales path in order, each step marked from
 * the person's own records and linked to where the work happens. Disappears
 * once the path is complete. "Applicant onboarding" (your paperwork) and
 * "customer onboarding" (handing a won customer to setup) are different things.
 */
export default function GettingStarted() {
  const [d, setD] = useState(null)
  const [hide, setHide] = useState(() => { try { return sessionStorage.getItem('evosys.gs.hide') === '1' } catch { return false } })
  useEffect(() => { api.get('/sales-team/getting-started', { noOrgContext: true }).then(setD).catch(() => setD(null)) }, [])
  if (!d || d.complete) return null
  const done = d.steps.filter(s => s.done && !s.optional).length
  const total = d.steps.filter(s => !s.optional).length
  return (
    <section className="ss-panel" aria-labelledby="gs-title" style={{ marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <h2 id="gs-title" style={{ margin: 0, fontSize: 18 }}>Getting started · {done} of {total}</h2>
        <button type="button" className="ss-btn ss-white ss-xs" aria-expanded={!hide}
                onClick={() => { const v = !hide; setHide(v); try { sessionStorage.setItem('evosys.gs.hide', v ? '1' : '0') } catch { /* ignore */ } }}>
          {hide ? 'Show steps' : 'Hide steps'}
        </button>
      </div>
      {d.next && (
        <p style={{ margin: '10px 0' }}>
          <b>Do this next:</b> {d.next.label}{' '}
          <Link className="ss-btn ss-xs" to={d.next.link}>{d.next.action}</Link>
          {d.next.key === 'prospect' && <> <Link className="ss-btn ss-white ss-xs" to="/sales/pipeline">Open my leads</Link></>}
        </p>
      )}
      {!hide && (
        <ol style={{ margin: '6px 0 0', paddingLeft: 22 }}>
          {d.steps.map(s => (
            <li key={s.key} style={{ marginBottom: 4, color: s.done ? '#0b6b4a' : undefined }}>
              {s.done ? '✓ ' : ''}{s.label}{s.optional ? ' (when needed)' : ''}
              {!s.done && <> — <Link to={s.link}>{s.action}</Link></>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
