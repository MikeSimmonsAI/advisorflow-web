/* Tarrant tax-roll misread (Oct 17-19, 2026): the targeted repair, on screen.
 *
 * GET  /wholesale/evosense/roll-fix/status          what is still in scope + past runs
 * POST /wholesale/evosense/roll-fix/dry-run         writes nothing; returns the plan and its code
 * POST /wholesale/evosense/roll-fix/apply           {confirm: "APPLY ROLL FIX <code>"} - typed exactly
 * POST /wholesale/evosense/roll-fix/{id}/rollback   {confirm: "ROLLBACK <id>"} - typed exactly
 *
 * Admin of this workspace only. The server recomputes the plan on apply and
 * refuses if the code no longer matches, so the typed code is the safeguard,
 * not this screen.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../wsShared'
import { Alert, Metric, Metrics, Panel } from '../ds/ds'

const n = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString())

export default function RollFixPanel() {
  const [status, setStatus] = useState(null)
  const [plan, setPlan] = useState(null)
  const [typed, setTyped] = useState('')
  const [undoTyped, setUndoTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(() => {
    api.get('/wholesale/evosense/roll-fix/status').then(setStatus).catch((e) => setErr(errText(e)))
  }, [])
  useEffect(() => { load() }, [load])

  async function preview() {
    setErr(''); setNote(''); setBusy(true); setTyped('')
    try { setPlan(await api.post('/wholesale/evosense/roll-fix/dry-run', {})) } catch (e) { setErr(errText(e)) }
    setBusy(false)
  }
  async function apply() {
    setErr(''); setNote(''); setBusy(true)
    try {
      const out = await api.post('/wholesale/evosense/roll-fix/apply', { confirm: typed })
      setNote(`Repair applied (run ${out.run_id}). ${n(out.totals?.signals_retracted)} false signals retracted.`)
      setPlan(null); setTyped(''); load()
    } catch (e) { setErr(errText(e)) }
    setBusy(false)
  }
  async function undo(id) {
    setErr(''); setNote(''); setBusy(true)
    try {
      await api.post(`/wholesale/evosense/roll-fix/${id}/rollback`, { confirm: undoTyped })
      setNote(`Run ${id} rolled back: every row restored.`); setUndoTyped(''); load()
    } catch (e) { setErr(errText(e)) }
    setBusy(false)
  }

  const t = plan?.totals || {}
  const applied = (status?.runs || []).find((r) => r.status === 'roll_fix_applied')
  return (
    <Panel title="Data repair: Tarrant tax-roll misread (Oct 17–19)" hint="admin only · preview writes nothing">
      <Alert>{err}</Alert>
      <Alert kind="ok">{note}</Alert>
      <p className="pc-sub" style={{ margin: '0 0 12px' }}>
        Records read with the old column layout: <b>{n(status?.in_scope)}</b> still to repair
        {status?.hunt_running ? ' · a hunt is running now — wait for it to finish' : ''}.
        Only those records are touched; other sources&apos; evidence, owners people entered, and phone records are kept.
      </p>
      <div className="pc-chips" style={{ gap: 8, display: 'flex', flexWrap: 'wrap' }}>
        <button type="button" className="evo-btn evo-btn--sm" disabled={busy || !status?.in_scope} onClick={preview}>
          Preview the repair (writes nothing)
        </button>
      </div>

      {plan ? (
        <div style={{ marginTop: 14 }}>
          <Metrics label="Repair preview">
            <Metric label="Records in scope" value={n(plan.scope?.observations)} sub={`${n(plan.scope?.properties)} properties`} />
            <Metric label="False signals removed" value={n(t.signals_retracted)} />
            <Metric label="Real delinquencies kept" value={n(t.still_tax_delinquent)} />
            <Metric label="Other sources kept" value={n(t.signals_preserved_other_sources)} />
            <Metric label="Owners re-linked / corrected / left alone"
                    value={`${n(t.owners_relinked_to_existing)} / ${n(t.owners_corrected)} / ${n(t.owners_ambiguous_left_alone)}`} />
            <Metric label="Cities corrected" value={n(t.situs_fields_corrected)} />
            <Metric label="At 45+ before → after" value={`${n(t.at_or_above_45_before)} → ${n(t.at_or_above_45_after)}`} />
            <Metric label="Unreadable lines left alone" value={n(t.unreadable_lines_left_alone)} />
          </Metrics>
          <label className="evo-field" htmlFor="rollfix-confirm" style={{ marginTop: 12, display: 'block' }}>
            <span className="evo-field__label">To apply, type exactly: <code>{plan.confirm_with}</code></span>
            <input id="rollfix-confirm" className="evo-input" value={typed} autoComplete="off"
                   onChange={(e) => setTyped(e.target.value)} />
          </label>
          <button type="button" className="evo-btn evo-btn--sm evo-btn--primary" style={{ marginTop: 8 }}
                  disabled={busy || typed !== plan.confirm_with} onClick={apply}>Apply the repair</button>
        </div>
      ) : null}

      {(status?.runs || []).length ? (
        <div style={{ marginTop: 16 }}>
          <h3 className="pc-h2" style={{ fontSize: 14 }}>Runs</h3>
          <ul className="pc-sub">
            {status.runs.map((r) => (
              <li key={r.id}>{r.id} · {r.status.replace(/_/g, ' ')} · {n(r.property_count)} properties
                {r.applied_at ? ` · applied ${r.applied_at}` : ''}{r.rolled_back_at ? ` · rolled back ${r.rolled_back_at}` : ''}</li>
            ))}
          </ul>
          {applied ? (
            <>
              <label className="evo-field" htmlFor="rollfix-undo" style={{ display: 'block' }}>
                <span className="evo-field__label">To undo, type exactly: <code>ROLLBACK {applied.id}</code> (refused once anything has changed since)</span>
                <input id="rollfix-undo" className="evo-input" value={undoTyped} autoComplete="off"
                       onChange={(e) => setUndoTyped(e.target.value)} />
              </label>
              <button type="button" className="evo-btn evo-btn--sm" style={{ marginTop: 8 }}
                      disabled={busy || undoTyped !== `ROLLBACK ${applied.id}`} onClick={() => undo(applied.id)}>Undo this repair</button>
            </>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}
