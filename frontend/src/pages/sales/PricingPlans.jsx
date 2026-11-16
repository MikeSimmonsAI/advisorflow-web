import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

/**
 * PRICING & PLANS — Back Office, /sales/pricing.
 *
 * What the EvoSys Pro Sales commission rules and package prices are, and who
 * changes them. Shown to God Mode, the owners and people granted team
 * compensation on the brand (the server decides). God Mode stays owner-only:
 * only a God Mode login gets the link into it.
 */
const card = { background: '#fff', border: '1px solid #dce5ea', borderRadius: 12, padding: 16, marginBottom: 12 }
const th = { textAlign: 'left', padding: '6px 8px', color: '#607486', fontSize: 12 }
const td = { padding: '6px 8px', borderTop: '1px solid #e6edf1' }
const usd = n => n === null || n === undefined ? '—' : '$' + Number(n).toLocaleString('en-US')

export default function PricingPlans() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => { api.get('/sales-team/pricing', { noOrgContext: true }).then(setD).catch(e => setErr((e && e.detail) || 'Could not load pricing.')) }, [])
  if (err) return <div style={card} role="alert">{err} <Link to="/sales/my-compensation">Back to Compensation</Link></div>
  if (!d) return <div style={card}>Loading…</div>
  const c = d.commission
  return (
    <div>
      <p style={{ margin: '0 0 12px' }}><Link to="/sales/compensation">← Team Compensation</Link></p>
      <div style={card}>
        <b>Why this page exists</b>
        <p style={{ margin: '6px 0 0' }}>These are the prices customers are quoted and the commission rules every quote, estimate and earned record uses for {d.brand.name}. Rules version <b>{d.rules_version}</b>. {d.how_to_change}</p>
        {d.can_edit_in_god_mode && <p style={{ margin: '8px 0 0' }}><Link to="/god/pricing">Edit in God Mode → Pricing</Link></p>}
      </div>
      <div style={card}>
        <b>Month 1 — flat payout on the package's one-time fee</b>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <thead><tr><th style={th}>Package</th><th style={th}>One-time fee</th><th style={th}>Salesperson</th><th style={th}>Area Sales Manager</th><th style={th}>Sr EVP</th></tr></thead>
          <tbody>{c.plans.filter(p => p.kind === 'package').map(p => (
            <tr key={p.key}><td style={td}>{p.name}</td><td style={td}>{usd(p.setup)}</td><td style={td}>{usd(p.payout.rep)}</td><td style={td}>{usd(p.payout.asm)}</td><td style={td}>{usd(p.payout.sevp)}</td></tr>
          ))}</tbody>
        </table>
        <p style={{ margin: '10px 0 0' }}>Month 1: the customer pays only the one-time fee, and these flat amounts are paid — they are not a percentage of the fee. No monthly fee or residual is due in Month 1.</p>
        <p style={{ margin: '6px 0 0' }}>Month 2 onward, on each package monthly fee collected: <b>{c.package_monthly_pct.rep}% / {c.package_monthly_pct.asm}% / {c.package_monthly_pct.sevp}%</b> (Salesperson / Area Sales Manager / Sr EVP). Custom SaaS setup and monthly: <b>{c.custom_saas_rates.rep}% / {c.custom_saas_rates.asm}% / {c.custom_saas_rates.sevp}%</b>. Package add-on one-time setup: <b>{c.addon_setup_rates ? `${c.addon_setup_rates.rep}% / ${c.addon_setup_rates.asm}% / ${c.addon_setup_rates.sevp}%` : '—'}</b> (unchanged). Commission is held {c.holdback_days} days after the customer pays.</p>
      </div>
      <div style={card}>
        <b>Customer package prices (back office catalogue)</b>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
          <thead><tr><th style={th}>Package</th><th style={th}>Setup</th><th style={th}>Monthly</th></tr></thead>
          <tbody>{d.packages.map(p => <tr key={p.key}><td style={td}>{p.name}</td><td style={td}>{usd(p.setup_fee)}</td><td style={td}>{usd(p.monthly_price)}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  )
}
