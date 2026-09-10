/**
 * GOD MODE — DEMO SUITE.
 *
 * One row per brand: does it have a demonstration environment, is it built, is
 * it current, and what is in it. Building one is two tenants and a seeded
 * world; it can never touch a real customer, and the screen says so rather
 * than leaving the operator to hope.
 *
 * WHY REBUILD IS NOT A CASUAL BUTTON. It re-seeds the world every presenter of
 * that brand is standing in, including anybody mid-meeting. It is confirmed,
 * and the confirmation says what it affects — which is the difference between
 * a warning and a shrug.
 *
 * Data:
 *   GET  /god/demo-suite/environments
 *   POST /god/demo-suite/environments/{platform}/build
 *   POST /god/demo-suite/environments/{platform}/reset
 *   GET  /god/demo-suite/events
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'
import ConfirmDialog from './ConfirmDialog'

const STATUS_TONE = { ready: 'ok', empty: 'off', building: 'pend', error: 'bad' }

function when (iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString()
}

export default function GodDemoSuite () {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [events, setEvents] = useState([])
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [confirm, setConfirm] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b] = await Promise.all([
        api.get('/god/demo-suite/environments'),
        api.get('/god/demo-suite/events?limit=40'),
      ])
      setData(a); setEvents(b.events || [])
    } catch (e) { setErr(e?.message || 'Could not load demonstration environments.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  async function run (platformId, what) {
    setBusy(platformId + what); setErr(''); setNotice('')
    try {
      const out = await api.post(
        '/god/demo-suite/environments/' + platformId + '/' + what)
      setNotice(what === 'build'
        ? `Built: ${out.workspace.leads} leads, ${out.workspace.messages} messages, ` +
          `${out.sales.opportunities} deals.`
        : 'The demonstration world was emptied. The two tenants are kept, so ' +
          'rebuilding it later reuses the same ids.')
      await load()
    } catch (e) { setErr(e?.message || 'That did not work.') }
    finally { setBusy(''); setConfirm(null) }
  }

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: '#fff', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            Demo Suite
          </h1>
          <p style={{ margin: '9px 0 0', color: '#758ba4', fontSize: 12, maxWidth: 760 }}>
            One demonstration environment per brand: an isolated customer
            workspace and an isolated sales organization, both flagged as
            demonstrations, seeded with believable fictional data. A demo action
            can only land on a flagged tenant, and the outbound paths refuse a
            flagged tenant outright — so nothing here can reach a real customer
            or a real phone.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'rgba(255,93,125,.35)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}
        {notice && (
          <div className="gm-card" style={{ borderColor: 'rgba(35,239,178,.35)', marginBottom: 16 }}>
            <div style={{ color: T.teal, fontSize: 12 }}>✓ {notice}</div>
          </div>
        )}

        <SectionLabel note={data ? `seed version ${data.seed_version}` : ''}>
          ENVIRONMENTS
        </SectionLabel>
        <div className="gm-card" style={{ padding: 0, marginBottom: 18 }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead><tr>
                <th>BRAND</th><th>STATE</th><th>WHAT IS IN IT</th>
                <th>BUILT</th><th>ACTIONS</th>
              </tr></thead>
              <tbody>
                {loading && <tr><td className="gm-empty" colSpan={5}>Loading…</td></tr>}
                {!loading && (data?.environments || []).length === 0 &&
                  <tr><td className="gm-empty" colSpan={5}>No brands on this platform.</td></tr>}
                {(data?.environments || []).map(e => (
                  <tr key={e.platform_id}>
                    <td>
                      <div className="gm-orgname">{e.platform_name}</div>
                      <div className="gm-orgsub">{e.platform_slug}</div>
                    </td>
                    <td>
                      <StatusBadge tone={STATUS_TONE[e.status] || 'off'}>
                        {e.exists ? e.status.toUpperCase() : 'NOT BUILT'}
                      </StatusBadge>
                      {e.stale && <div style={{ color: T.amber, fontSize: 10.5, marginTop: 4 }}>
                        built from an older seed
                      </div>}
                      {e.last_error && <div style={{ color: T.red, fontSize: 10.5, marginTop: 4 }}>
                        {e.last_error}
                      </div>}
                    </td>
                    <td>
                      {e.ready ? (
                        <>
                          <div style={{ fontSize: 11.5 }}>{e.workspace_name}</div>
                          <div className="gm-orgsub">
                            {e.counts.leads} leads · {e.counts.opportunities} deals ·
                            {' '}{e.counts.staff} people
                          </div>
                          <div className="gm-orgsub">
                            {e.counts.sessions} presenter session(s) ·
                            {' '}{e.counts.actions} demo actions
                          </div>
                        </>
                      ) : <NoSource>nothing seeded</NoSource>}
                    </td>
                    <td style={{ fontSize: 11.5 }}>
                      {when(e.seeded_at) || <NoSource>never</NoSource>}
                    </td>
                    <td>
                      <div className="gm-acts">
                        <button className="gm-act gm-primary"
                                disabled={busy === e.platform_id + 'build'}
                                onClick={() => setConfirm({
                                  platform_id: e.platform_id, name: e.platform_name,
                                  what: 'build', exists: e.exists,
                                })}>
                          {e.exists ? 'REBUILD' : 'BUILD'}
                        </button>
                        {e.exists && (
                          <button className="gm-act gm-danger"
                                  disabled={busy === e.platform_id + 'reset'}
                                  onClick={() => setConfirm({
                                    platform_id: e.platform_id, name: e.platform_name,
                                    what: 'reset', exists: true,
                                  })}>
                            EMPTY
                          </button>
                        )}
                        {e.ready && (
                          <button className="gm-act"
                                  onClick={() => navigate('/demo-suite/' + e.platform_id)}>
                            OPEN
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <SectionLabel note="what each scenario demonstrates">GUIDED SCENARIOS</SectionLabel>
        <div className="gm-card" style={{ padding: 0, marginBottom: 18 }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead><tr><th>SCENARIO</th><th>FOR</th><th>LENGTH</th><th>STEPS</th><th>WHAT IT SHOWS</th></tr></thead>
              <tbody>
                {(data?.scenarios || []).map(s => (
                  <tr key={s.key}>
                    <td><div className="gm-orgname">{s.name}</div></td>
                    <td><span className="gm-pill">{s.audience}</span></td>
                    <td>{s.minutes} min</td>
                    <td>{s.total_steps}</td>
                    <td style={{ maxWidth: 560, color: T.dim, fontSize: 11.5 }}>{s.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <SectionLabel note="the demo's own trail — deliberately not the audit log">
          RECENT DEMO ACTIVITY
        </SectionLabel>
        <div className="gm-card" style={{ padding: 0 }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead><tr><th>WHEN</th><th>WHO</th><th>ACTION</th><th>SCENARIO</th><th>SIMULATED</th><th>DETAIL</th></tr></thead>
              <tbody>
                {events.length === 0 &&
                  <tr><td className="gm-empty" colSpan={6}>Nobody has presented yet.</td></tr>}
                {events.map(e => (
                  <tr key={e.id}>
                    <td style={{ fontSize: 11.5 }}>{when(e.at)}</td>
                    <td style={{ fontSize: 11.5 }}>{e.who || <NoSource>—</NoSource>}</td>
                    <td><span className="gm-pill blue">{e.action}</span></td>
                    <td style={{ fontSize: 11.5 }}>{e.scenario || <NoSource>off-script</NoSource>}</td>
                    <td>{e.simulated_provider
                      ? <span className="gm-pill amber">{e.simulated_provider}</span>
                      : <NoSource>none</NoSource>}</td>
                    <td style={{ maxWidth: 460, color: T.dim, fontSize: 11 }}>{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {confirm && (
        <ConfirmDialog
          tone={confirm.what === 'reset' ? 'danger' : 'blue'}
          eyebrow={confirm.name}
          title={confirm.what === 'reset'
            ? 'Empty this demonstration world?'
            : confirm.exists ? 'Rebuild this demonstration world?'
                             : 'Build this demonstration world?'}
          body={confirm.what === 'reset'
            ? 'Every seeded record is removed and the brand has nothing to ' +
              'present until it is built again. The two demonstration tenants ' +
              'are kept, so rebuilding later reuses the same ids. No real ' +
              'customer is touched.'
            : 'This re-seeds the shared world for EVERYBODY presenting this ' +
              'brand, including anybody in a meeting right now. It creates ' +
              'believable fictional records inside two demonstration tenants ' +
              'and cannot touch a real customer.'}
          confirmLabel={confirm.what === 'reset' ? 'EMPTY IT' : 'BUILD IT'}
          busy={!!busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => run(confirm.platform_id, confirm.what)} />
      )}
    </div>
  )
}
