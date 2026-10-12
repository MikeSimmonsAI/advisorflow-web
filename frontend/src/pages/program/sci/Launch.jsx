/* Launch readiness: the live checklist from GET /program/readiness-test,
   grouped into stages A-G (A code QA · B inbound live test · C consent
   records · D carrier-approved purpose · E admin assignment · F production ·
   G first-contact GO), a truthful Go/No-Go verdict near the top, the owner's
   pending decisions, and the controlled SYNTHETIC test
   (POST /readiness-test/run - synthetic records, nothing is sent).
   A synthetic pass is never presented as a live test or as owner approval. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Icon, Kpi, Loading, PageHead, Panel, errText, num, when } from './ui'

const VERDICT_TONE = { READY: 'ok', CONDITIONAL: 'warn', BLOCKED: 'bad' }
const GROUP_STATE = {
  verified: ['Complete', 'ok', '✓'], test_now: ['Test now', 'info', '•'],
  external: ['Pending', 'warn', '!'], approval: ['Your decision', 'bad', '?'],
}
const FALLBACK_STAGES = { A: 'Code QA', B: 'Inbound live test', C: 'Consent records', D: 'Carrier-approved message purpose', E: 'Admin assignment', F: 'Production', G: 'First-contact GO' }

function stageState(items) {
  if (!items.length) return ['No checks reported', '', '–']
  if (items.some(x => x.group === 'approval')) return ['Your decision', 'bad', '?']
  if (items.some(x => x.group === 'external')) return ['Pending', 'warn', '!']
  if (items.some(x => x.group === 'test_now')) return ['Test now', 'info', '•']
  return ['Complete', 'ok', '✓']
}

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
  if (!d) return <><PageHead title="Launch Readiness" />{err ? <ErrorLine text={err} /> : <Loading />}</>

  const stages = d.stages || FALLBACK_STAGES
  const all = ['verified', 'test_now', 'external', 'approval'].flatMap(g => (d.groups[g] || []).map(x => ({ ...x, group: g })))
  const byStage = Object.keys(stages).map(s => ({ s, name: stages[s], items: all.filter(x => (x.stage || '?') === s) }))
  const unstaged = all.filter(x => !x.stage || !stages[x.stage])
  const openItems = all.filter(x => x.group === 'external' || x.group === 'approval')
  const decisions = d.groups.approval || []
  const last = d.last_run
  const verdict = d.verdict || { status: 'BLOCKED', reason: 'Not reported by the server.' }
  const cleared = openItems.length === 0
  const textingOn = sms ? !!sms.sending_enabled : null
  const why = Array.isArray(d.why_no_go) ? d.why_no_go : []

  return (
    <>
      <PageHead title="Launch Readiness" sub="A truthful route from staging checks to the first approved family contact.">
        <Chip tone={cleared ? 'ok' : 'bad'}>{cleared ? 'Cleared for first contact' : 'Launch blocked'}</Chip>
        {isManager && <button type="button" className="sci-btn gold" disabled={busy} onClick={run}><Icon name="play" />{busy ? 'Running…' : 'Run controlled test'}</button>}
      </PageHead>

      <section className="sci-kpis four" aria-label="Readiness numbers">
        <Kpi icon="launch" tone="blue" value={Object.keys(stages).length} label="Readiness stages" note="A to G" />
        <Kpi icon="check" tone="green" value={(d.groups.verified || []).length} label="Checks verified" note="Done and proven" />
        <Kpi icon="alert" tone="gold" value={openItems.length} label="Open items" note="Pending or your decision" />
        <Kpi icon="user" tone="red" value={decisions.length} label="Owner decisions" note="Only you can clear these" />
      </section>
      <ErrorLine text={err} />

      <div className="sci-grid-review" style={{ gridTemplateColumns: 'minmax(0, 1fr) 380px' }}>
        <Panel title="Readiness gates" labelledBy="sci-gates" aside={<span className="sci-micro sci-muted">Live from the server</span>}>
          <div className={`sci-banner ${cleared ? 'ok' : 'bad'}`} style={{ marginBottom: 12 }}>
            <span className="sci-banner-icon" aria-hidden="true"><Icon name={cleared ? 'check' : 'ban'} /></span>
            <div className="sci-banner-body">
              <strong>First-contact status: {cleared ? 'CLEARED' : 'NOT CLEARED'}</strong>
              <span className="sci-micro">{cleared ? 'Every checklist item is complete.' : `${openItems.length} item(s) still open, including the first-contact GO. Do not treat a staging pass as proof that live family outreach is authorized.`}</span>
            </div>
          </div>
          {byStage.map(st => {
            const [label, tone, mark] = stageState(st.items)
            return (
              <details key={st.s} className="sci-stagecard">
                <summary>
                  <span className={`sci-stage-letter${tone === 'ok' ? ' ok' : ''}`} aria-hidden="true">{st.s}</span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <b style={{ fontSize: 13 }}>{st.name}</b>
                    <span className="sci-micro sci-muted" style={{ display: 'block' }}>{st.items.length ? `${st.items.filter(x => x.group === 'verified').length} of ${st.items.length} complete` : 'No checks reported for this stage'}</span>
                  </span>
                  <Chip tone={tone} plain><span aria-hidden="true">{mark}</span> {label}</Chip>
                </summary>
                {st.items.length > 0 && (
                  <div className="sci-stage-body sci-rows">
                    {st.items.map(g => <CheckItem key={g.key} g={g} />)}
                  </div>
                )}
              </details>
            )
          })}
          {unstaged.length > 0 && (
            <details className="sci-stagecard" open>
              <summary><span className="sci-stage-letter" aria-hidden="true">·</span><b style={{ flex: 1 }}>Other</b></summary>
              <div className="sci-stage-body sci-rows">{unstaged.map(g => <CheckItem key={g.key} g={g} />)}</div>
            </details>
          )}
        </Panel>

        <div className="sci-stack">
          <Panel title="Go / No-Go decision" labelledBy="sci-gonogo">
            <div className="sci-verdict">
              <span className={`sci-verdict-icon ${cleared ? 'ok' : 'bad'}`} aria-hidden="true"><Icon name={cleared ? 'check' : 'alert'} /></span>
              <strong>{cleared ? 'Cleared for first contact' : 'Not cleared for outreach'}</strong>
              <span className="sci-micro sci-muted">Open requirements and the final first-contact approval must be cleared before production outreach.</span>
            </div>
            {why.length > 0 && (
              <div className="sci-rows" style={{ marginTop: 10 }}>
                {why.map((t, i) => (
                  <div className="sci-decision" key={i}><span className="sci-decision-n">{i + 1}.</span><span className="sci-small">{typeof t === 'string' ? t : t.text}</span></div>
                ))}
              </div>
            )}
            <div className="sci-ctrl" style={{ marginTop: 6 }}>
              <span>Outbound texting switch</span>
              <button type="button" className="sci-more" onClick={() => goTab('health')}>
                {textingOn === null ? 'Unknown' : textingOn ? 'ON' : 'OFF'}{sms?.number ? ` · ${sms.number}` : ''}<Icon name="arrow" />
              </button>
            </div>
          </Panel>

          <Panel title="Controlled readiness test" labelledBy="sci-ctest" aside={<Chip tone={VERDICT_TONE[verdict.status]} plain>{verdict.status}</Chip>}>
            <div className="sci-ctrl"><span>Latest test</span><b>{last ? when(last.ran_at) : 'Not run on this server'}</b></div>
            <div className="sci-ctrl"><span>Synthetic messages</span><span className="sci-muted">Never counted as live delivery</span></div>
            {last && <div className="sci-ctrl"><span>Result</span><b>{last.pass_count} pass · {last.fail_count} fail · {num(last.sent)} sent</b></div>}
            <p className="sci-micro sci-muted" style={{ margin: '8px 0' }}>{verdict.reason} {d.note}</p>
            {isManager ? (
              <button type="button" className="sci-btn gold block" disabled={busy} onClick={run}><Icon name="play" />{busy ? 'Running…' : 'Run controlled test'}</button>
            ) : <p className="sci-micro sci-muted">Only a workspace manager can run the controlled test.</p>}
            {last && (
              <div style={{ marginTop: 10 }}>
                {last.failed_gate && <div className="sci-alert" role="alert"><Icon name="alert" />Failed gate: {last.failed_gate}</div>}
                {last.next_action && <p className="sci-micro sci-muted">Next action: {last.next_action}</p>}
                <details className="sci-details">
                  <summary>Check-by-check results ({(last.results || []).length + (last.webhook_proof?.results || []).length})</summary>
                  <div className="sci-details-body sci-rows">
                    {[...(last.results || []), ...(last.webhook_proof?.results || [])].map(r => (
                      <div className="sci-kv" key={r.key}>
                        <span>{r.label}{r.error && <span className="sci-micro sci-muted"> — {r.error}</span>}</span>
                        <span><Chip tone={r.status === 'PASS' ? 'ok' : 'bad'} plain>{r.status}</Chip></span>
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            )}
            <div className="sci-banner" style={{ margin: '10px 0 0' }}>
              <span className="sci-banner-icon" aria-hidden="true"><Icon name="shield" /></span>
              <div className="sci-banner-body sci-micro">Testing never sends messages to real families, and a passing test is not owner approval.</div>
            </div>
          </Panel>

          <Panel title="Your decisions" labelledBy="sci-decisions">
            {decisions.length === 0 ? <Empty icon="check">No decisions waiting.</Empty> : (
              <div className="sci-rows">
                {decisions.map((g, i) => (
                  <div className="sci-decision" key={g.key}><span className="sci-decision-n">{i + 1}.</span><span className="sci-small">{g.text}</span></div>
                ))}
              </div>
            )}
          </Panel>
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
