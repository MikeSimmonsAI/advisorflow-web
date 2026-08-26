/**
 * Sales Workspace — what happened after Won (Checkpoint 6 §15, §16).
 *
 * A rep sees the deals they sold. A manager sees their brand's. Both see the
 * same coarse projection: where the customer got to, who owns it, when it is
 * meant to launch, and whether it is stuck.
 *
 * WHAT IS DELIBERATELY ABSENT. No tenant leads, no customer users, no
 * communications, no milestone detail, no internal notes, and no blocker text —
 * a blocker often names a customer's staffing problem, and the person who sold
 * the deal has no reason to be holding it. The server does not send those fields
 * at all; this screen could not show them if it tried.
 *
 * IT USES THE SALES WORKSPACE'S OWN SHELL AND `sw-` CLASSES, not God Mode's
 * `go-` sheet. It is a sales screen and belongs to that theme; dropping a dark
 * God Mode panel into the light sales workspace looked exactly as wrong as it
 * sounds, which the first round of screenshots showed plainly.
 */
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import SalesShell from './sales/SalesShell'
import { Card, Chip, Empty, ErrorBar, shortDate } from './sales/parts'

function tone(row) {
  if (row.is_live) return 'do'
  if (row.is_blocked) return 'attn'
  return null
}

export default function SalesImplementations() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState(null)

  const load = () => {
    setError(null)
    api.get('/sales/implementations')
      .then(r => setRows(Array.isArray(r) ? r : []))
      .catch(e => { setError(e.message || 'Could not load.'); setRows([]) })
  }
  useEffect(load, [])

  return (
    <SalesShell
      title="Sold / Onboarding"
      subtitle="Read-only progress for the customers your deals became."
      actions={<button className="sw-btn" onClick={load}>Refresh</button>}
    >
      <ErrorBar error={error} onRetry={load} />

      {rows === null ? <div className="sw-subtle">Loading…</div> : (
        <Card title="Customers" bodyless>
          {!rows.length ? (
            <div className="sw-card-b">
              <Empty title="Nothing here yet">
                None of your Won deals have been provisioned into a customer
                organisation. Provisioning is a deliberate step, not something
                that happens the moment a deal closes.
              </Empty>
            </div>
          ) : (
            <div className="sw-tablewrap">
              <table className="sw-table">
                <thead>
                  <tr>
                    <th>Customer</th>
                    <th>Status</th>
                    <th>Implementation owner</th>
                    <th>Onboarding</th>
                    <th>Launch</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.implementation_id}>
                      <td><strong>{r.customer_organization_name}</strong></td>
                      <td>
                        <Chip tone={tone(r)}>{r.status_label}</Chip>
                        {r.is_blocked && r.blocked_since ? (
                          <div className="sw-subtle">since {shortDate(r.blocked_since)}</div>
                        ) : null}
                      </td>
                      <td>{r.implementation_owner || <span className="sw-subtle">unassigned</span>}</td>
                      <td>{r.percent_complete}%</td>
                      <td>
                        {r.is_live
                          ? <Chip tone="do">live {shortDate(r.launched_at)}</Chip>
                          : (r.target_launch_date
                             ? shortDate(r.target_launch_date)
                             : <span className="sw-subtle">not set</span>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      <p className="sw-subtle" style={{ marginTop: 14 }}>
        You keep visibility after the sale; you do not get access to the
        customer's own data. Their leads, users and conversations stay inside
        their tenant.
      </p>
    </SalesShell>
  )
}
