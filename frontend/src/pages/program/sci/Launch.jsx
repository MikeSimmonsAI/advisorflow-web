/* Launch readiness: the current checklist from GET /program/readiness-test,
   grouped by stage (A code QA · B inbound live test · C consent records ·
   D carrier-approved purpose · E admin assignment · F production · G first-
   contact GO), the owner's decisions, and the controlled synthetic test.
   A synthetic check is never shown as a live test. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Banner, Chip, Empty, ErrorLine, Loading, Metric, PageHead, errText, num, when } from './ui'

const VERDICT_TONE = { READY: 'ok', CONDITIONAL: 'warn', BLOCKED: 'bad' }
const GROUP_STATE = {
  verified: ['Complete', 'ok', '✓'], test_now: ['Test now', 'info', '•'],
  external: ['Pending', 'warn', '!'], approval: ['Your decision', 'bad', '?'],
}
const FALLBACK_STAGES = { A: 'Code QA', B: 'Inbound live test', C: 'Consent records', D: 'Carrier-approved message purpose', E: 'Admin assignment', F: 'Production', G: 'First-contact GO' }

export default function Launch({ isManager, onChange, goTab }) {
  const [d, setD] = useState(null)
  const [sms, setSms] = useState(undefined)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => { api.get('/program/readiness-test').then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e))) }, [])
  useEffect(() => { load(); api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => setSms(null)) }, [load])
  async function run() {
    setBusy(true); setErr('')
    try { setD(await api.post('/program/readiness-test/run', {})); onChange && onChange() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (!d) return <><PageHead title="Launch readiness" />{err ? <ErrorLine text={err} /> : <Loading />}</>

  const stages = d.stages || FALLBACK_STAGES
  const all = ['verified', 'test_now', 'external', 'approval'].flatMap(g => (d.groups[g] || []).map(x => ({ ...x, group: g })))
  const byStage = Object.keys(stages).map(s => ({ s, name: stages[s], items: all.filter(x => (x.stage || '?') === s) })).filter(x => x.items.length)
  const unstaged = all.filter(x => !x.stage || !stages[x.stage])
  const openItems = all.filter(x => x.group === 'external' || x.group === 'approval')
  const decisions = [...(d.groups.approval || []), ...(d.groups.test_now || [])]
  const last = d.last_run
  const verdict = d.verdict
  const textingOn = sms ? !!sms.sending_enabled : null

  return (
    <>
      <PageHead title="Launch readiness" sub="A truthful path from staging checks to real family outreach." />
      <Banner tone={openItems.length ? 'bad' : 'ok'} title={openItems.length ? 'Not cleared for first contact yet' : 'All launch items cleared'}>
        {openItems.length ? `${openItems.length} item(s) still open below, including the first-contact GO.` : 'Every checklist item is complete.'}{' '}
        Staging build: <b>{verdict.status}</b> — {verdict.reason}
      </Banner>
      <ErrorLine text={err} />

      <section className="sci-metrics" aria-label="Readiness numbers">
        <Metric label="Verified items" value={(d.groups.verified || []).length} tone="ok" note="Done and proven" />
        <Metric label="Open items" value={openItems.length} tone={openItems.length ? 'warn' : 'ok'} note="Pending or your decision" />
        <Metric label="Outbound texting" value={textingOn === null ? 'Unknown' : textingOn ? 'ON' : 'OFF'} tone={textingOn ? 'ok' : 'bad'}
          note={sms ? `Server switch · ${sms.number || 'no number'}` : 'Health check unavailable'} onClick={() => goTab('health')} />
        <Metric label="Controlled test" value={last ? `${last.pass_count}/${last.pass_count + last.fail_count}` : 'Not run'}
          tone={last ? (last.fail_count ? 'bad' : 'ok') : 'warn'} note={last ? `Synthetic · ${when(last.ran_at)}` : 'Synthetic checks, no sends'} />
      </section>

      <div className="sci-grid-2">
        <section className="sci-panel sci-pad" aria-labelledby="sci-checklist">
          <h2 id="sci-checklist" className="sci-h2" style={{ marginBottom: 6 }}>Readiness checklist</h2>
          {byStage.map(st => (
            <div key={st.s} style={{ marginTop: 14 }}>
              <div className="sci-stage">{st.s} · {st.name}</div>
              <div className="sci-rows">
                {st.items.map(g => <CheckItem key={g.key} g={g} />)}
              </div>
            </div>
          ))}
          {unstaged.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div className="sci-stage">Other</div>
              <div className="sci-rows">{unstaged.map(g => <CheckItem key={g.key} g={g} />)}</div>
            </div>
          )}
        </section>

        <div className="sci-stack">
          <section className="sci-panel sci-pad" aria-labelledby="sci-decisions">
            <h2 id="sci-decisions" className="sci-h2">Next decisions</h2>
            {decisions.length === 0 ? <Empty>No decisions waiting.</Empty> : (
              <div className="sci-rows">
                {decisions.map((g, i) => (
                  <div className="sci-decision" key={g.key}>
                    <span className="sci-decision-n">{i + 1}.</span>
                    <span className="sci-small">{g.text}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="sci-panel sci-pad" aria-labelledby="sci-ctest">
            <div className="sci-panel-head">
              <h2 id="sci-ctest">Controlled test</h2>
              <Chip tone={VERDICT_TONE[verdict.status]}>{verdict.status}</Chip>
            </div>
            <p className="sci-small sci-muted" style={{ marginTop: 0 }}>{d.note} A synthetic check is not a real phone test.</p>
            {isManager && <button type="button" className="sci-btn primary" disabled={busy} onClick={run}>{busy ? 'Running…' : 'Run controlled readiness test'}</button>}
            {last && (
              <div style={{ marginTop: 14 }}>
                <p className="sci-small" style={{ margin: '0 0 6px' }}><b>{last.pass_count} PASS · {last.fail_count} FAIL</b> <span className="sci-muted">at {when(last.ran_at)} · {num(last.sent)} messages sent</span></p>
                {last.failed_gate && <div className="sci-alert" role="alert">Failed gate: {last.failed_gate}</div>}
                <p className="sci-micro sci-muted">Next action: {last.next_action}</p>
                {last.webhook_proof && (
                  <p className="sci-small"><b>Signed webhook proof: {last.webhook_proof.pass_count} PASS · {last.webhook_proof.fail_count} FAIL</b></p>
                )}
                <div className="sci-rows">
                  {(last.webhook_proof ? [...last.results, ...last.webhook_proof.results] : last.results).map(r => (
                    <div className="sci-kv" key={r.key}>
                      <span>{r.label}{r.error && <span className="sci-micro sci-muted"> — {r.error}</span>}</span>
                      <span><Chip tone={r.status === 'PASS' ? 'ok' : 'bad'} plain>{r.status}</Chip></span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  )
}

function CheckItem({ g }) {
  const [label, tone, mark] = GROUP_STATE[g.group] || ['—', '', '•']
  return (
    <div className="sci-check-item">
      <span className={`sci-check-mark ${tone}`} aria-hidden="true">{mark}</span>
      <span className="sci-small" style={{ flex: 1 }}>{g.text}</span>
      <Chip tone={tone} plain>{label}</Chip>
    </div>
  )
}
