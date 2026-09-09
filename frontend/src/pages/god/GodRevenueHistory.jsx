/**
 * GodRevenueHistory — Platform revenue history screen.
 *
 * REPORT-02: consumes GET /god/revenue-history.
 * Renders only data that actually exists:
 *   - Payments by month (real history from BillingPayment)
 *   - Invoice status breakdown (from BillingInvoice)
 *   - Plan breakdown (by billing_plan_key)
 *
 * Metrics that require a snapshot table that does not exist yet are shown
 * as clearly-labelled "unavailable" cards. Nothing is manufactured.
 */
import { useState, useEffect } from 'react'
import { api } from '../../api/client'

const fmt = cents =>
  cents == null ? '—'
  : '$' + (cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const STATUS_COLORS = {
  paid:      '#22c55e',
  open:      '#3b82f6',
  past_due:  '#ef4444',
  void:      '#9ca3af',
  draft:     '#d1d5db',
  uncollectible: '#f97316',
  unknown:   '#6b7280',
}

function SectionHead({ children, sub }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                    letterSpacing: '.07em', color: '#6b7280' }}>{children}</div>
      {sub && <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Card({ children, style }) {
  return (
    <div style={{
      background: 'var(--god-card, #fff)',
      border: '1px solid var(--god-border, #e5e7eb)',
      borderRadius: 10, padding: '20px 24px', marginBottom: 20,
      ...style,
    }}>
      {children}
    </div>
  )
}

/** Simple SVG bar chart — no external lib. */
function BarChart({ rows, valueKey, labelKey, color = '#3b82f6' }) {
  if (!rows || rows.length === 0) return (
    <div style={{ color: '#9ca3af', fontSize: 12, padding: '16px 0' }}>
      No payment records yet.
    </div>
  )
  const max = Math.max(...rows.map(r => r[valueKey] || 0), 1)
  const barW = Math.max(12, Math.min(40, Math.floor(560 / rows.length) - 4))
  const chartH = 100

  return (
    <div style={{ overflowX: 'auto' }}>
      <svg width={rows.length * (barW + 4)} height={chartH + 32} style={{ display: 'block' }}>
        {rows.map((r, i) => {
          const barH = Math.max(2, Math.round(((r[valueKey] || 0) / max) * chartH))
          const x = i * (barW + 4)
          const y = chartH - barH
          return (
            <g key={r[labelKey]} transform={`translate(${x},0)`}>
              <title>{r[labelKey]}: {fmt(r[valueKey])}</title>
              <rect x={0} y={y} width={barW} height={barH} fill={color} rx={2} opacity={0.85} />
              {rows.length <= 18 && (
                <text x={barW / 2} y={chartH + 14} textAnchor="middle"
                      fontSize={8} fill="#9ca3af">
                  {r[labelKey]?.slice(5) || r[labelKey]}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {rows.length > 18 && (
        <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 4 }}>
          {rows[0]?.[labelKey]} — {rows[rows.length - 1]?.[labelKey]}
        </div>
      )}
    </div>
  )
}

function UnavailableCard({ item }) {
  return (
    <div style={{
      border: '1px solid #e5e7eb', borderRadius: 8, padding: '14px 18px',
      marginBottom: 10, background: '#f9fafb',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: '#9ca3af', fontWeight: 600 }}>
          {item.label}
        </span>
        <span style={{
          fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 100,
          background: '#f3f4f6', color: '#6b7280', border: '1px solid #e5e7eb',
        }}>
          UNAVAILABLE
        </span>
      </div>
      <div style={{ fontSize: 11, color: '#9ca3af', lineHeight: 1.6 }}>
        {item.reason}
      </div>
    </div>
  )
}

export default function GodRevenueHistory() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshed, setRefreshed] = useState(null)
  const [platformFilter, setPlatformFilter] = useState('')
  const [platforms, setPlatforms] = useState([])

  const load = async (pid) => {
    setLoading(true); setError(null)
    try {
      const [histRes, platRes] = await Promise.all([
        api.get('/god/revenue-history', { params: pid ? { platform_id: pid } : {} }),
        api.get('/god/platforms'),
      ])
      setData(histRes.data)
      setPlatforms(platRes.data?.platforms || [])
      setRefreshed(new Date())
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load revenue history')
    } finally { setLoading(false) }
  }

  useEffect(() => { load(platformFilter) }, [platformFilter])

  const page = {
    padding: '24px 32px', maxWidth: 960,
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, #1f2937)',
  }

  return (
    <div style={page}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Revenue History</h1>
          <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 14 }}>
            Real payment and invoice history — no manufactured trends
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select value={platformFilter}
                  onChange={e => setPlatformFilter(e.target.value)}
                  style={{ fontSize: 12, padding: '5px 8px', borderRadius: 6,
                           border: '1px solid #e5e7eb', background: '#fff' }}>
            <option value="">All platforms</option>
            {platforms.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button onClick={() => load(platformFilter)} disabled={loading} style={{
            fontSize: 12, padding: '6px 14px', borderRadius: 6,
            border: '1px solid var(--god-border, #e5e7eb)',
            background: 'var(--god-card, #fff)', cursor: 'pointer',
          }}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5',
                      borderRadius: 8, padding: 16, color: '#dc2626', marginBottom: 20 }}>
          {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ color: '#9ca3af', padding: 24 }}>Loading…</div>
      )}

      {data && (
        <>
          {/* KPI summary row */}
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 20 }}>
            {[
              { label: 'Total collected', value: fmt(data.total_collected_cents) },
              { label: 'Payments',        value: data.total_payment_count.toLocaleString() },
              { label: 'Invoices',        value: data.total_invoice_count.toLocaleString() },
              { label: 'Data since',      value: data.data_since
                  ? new Date(data.data_since).toLocaleDateString()
                  : 'no payments yet' },
            ].map(({ label, value }) => (
              <div key={label} style={{
                flex: '1 1 160px', border: '1px solid #e5e7eb', borderRadius: 10,
                padding: '14px 18px', background: '#fff',
              }}>
                <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 6 }}>{label}</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
              </div>
            ))}
          </div>

          {/* Payments by month */}
          <Card>
            <SectionHead sub="From BillingPayment.paid_at — real payment records only">
              Collected by Month
            </SectionHead>
            <BarChart rows={data.payments_by_month}
                      valueKey="collected_cents" labelKey="month" color="#22c55e" />
          </Card>

          {/* Invoice status breakdown */}
          <Card>
            <SectionHead sub="From BillingInvoice.status">Invoice Status Breakdown</SectionHead>
            {data.invoice_status_breakdown.length === 0
              ? <div style={{ color: '#9ca3af', fontSize: 12 }}>No invoices recorded yet.</div>
              : data.invoice_status_breakdown.map(row => {
                  const color = STATUS_COLORS[row.status] || '#9ca3af'
                  return (
                    <div key={row.status} style={{
                      display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', padding: '8px 0',
                      borderBottom: '1px solid #f3f4f6',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ width: 10, height: 10, borderRadius: '50%',
                                       background: color, display: 'inline-block' }} />
                        <span style={{ fontSize: 13, color: '#1f2937' }}>{row.status}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 20 }}>
                        <span style={{ fontSize: 12, color: '#6b7280' }}>
                          {row.count} invoice{row.count !== 1 ? 's' : ''}
                        </span>
                        <span style={{ fontSize: 13, fontWeight: 600, color }}>
                          {fmt(row.paid_cents)} paid
                        </span>
                      </div>
                    </div>
                  )
                })
            }
          </Card>

          {/* Plan breakdown */}
          <Card>
            <SectionHead sub="From BillingInvoice.billing_plan_key">Plan Breakdown</SectionHead>
            {data.plan_breakdown.length === 0
              ? <div style={{ color: '#9ca3af', fontSize: 12 }}>No plan data yet.</div>
              : data.plan_breakdown.map(row => (
                  <div key={row.plan_key} style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '7px 0', borderBottom: '1px solid #f3f4f6',
                  }}>
                    <span style={{ fontSize: 13, fontFamily: 'monospace', color: '#1f2937' }}>
                      {row.plan_key}
                    </span>
                    <div style={{ display: 'flex', gap: 20 }}>
                      <span style={{ fontSize: 12, color: '#6b7280' }}>
                        {row.invoice_count} invoice{row.invoice_count !== 1 ? 's' : ''}
                      </span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: '#22c55e' }}>
                        {fmt(row.collected_cents)}
                      </span>
                    </div>
                  </div>
                ))
            }
          </Card>

          {/* Unavailable metrics */}
          <Card>
            <SectionHead sub="These metrics require a snapshot table that does not yet exist">
              Unavailable — Not Manufactured
            </SectionHead>
            {data.unavailable.map(u => <UnavailableCard key={u.metric} item={u} />)}
          </Card>

          {refreshed && (
            <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>
              As of {refreshed.toLocaleTimeString()}
            </div>
          )}
        </>
      )}
    </div>
  )
}
