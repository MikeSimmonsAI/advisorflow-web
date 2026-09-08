/**
 * ExecutiveTeam - who sells for this brand.
 *
 * WHAT THIS PAGE HONESTLY IS. `/executive/team` returns the ROSTER: every
 * active sales manager and rep across the brand's sales organizations, with
 * the org they sit in and when they joined. That is a real, useful answer to
 * "who works for me" and it is what this page shows.
 *
 * WHAT IT DELIBERATELY DOES NOT CLAIM. There is no per-person production
 * figure here - no deals closed, no quota, no leaderboard. The platform can
 * attribute an opportunity to an owner, but a salesperson's performance is a
 * compensation-adjacent judgement, and compensation visibility is its own
 * brand-scoped capability that an executive grant does not include. Rendering
 * a ranked table of people from data this surface is not authorized to read
 * would be the worst kind of dead feature: one that looks authoritative.
 *
 * So the roster is the roster, and the sentence at the bottom says where the
 * production numbers live and who can see them. Naming the gap is a better
 * product than filling it with something that cannot be trusted.
 */

import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Val, day, num, plural } from './ExecUI'

const ROLE_LABELS = {
  sales_manager: 'Sales manager',
  sales_rep: 'Sales rep',
  brand_executive: 'Executive',
}

export default function ExecutiveTeam() {
  const [state, setState] = useState({ loading: true, data: null, error: null })

  useEffect(() => {
    api.get('/executive/team')
      .then(r => setState({ loading: false, data: r, error: null }))
      .catch(() => setState({
        loading: false, data: null,
        error: 'Could not load your team. Please try again.' }))
  }, [])

  const { loading, data, error } = state
  if (loading) return <p className="ex-muted">Loading your team…</p>
  if (error || !data) return <div className="ex-err">{error || 'No data.'}</div>

  const team = data.team || []

  // Grouped by sales organization, because that is how a brand with more than
  // one team is actually structured and a flat list loses it.
  const groups = []
  const index = new Map()
  for (const m of team) {
    const key = m.brand_sales_org_id || 'none'
    if (!index.has(key)) {
      index.set(key, { name: m.brand_sales_org_name || 'Unassigned', members: [] })
      groups.push(index.get(key))
    }
    index.get(key).members.push(m)
  }

  return (
    <>
      <div className="ex-head">
        <div>
          <p className="ex-eyebrow">Portfolio</p>
          <h1 className="ex-h1">Sales team</h1>
          <p className="ex-sub">
            Everyone selling for this brand, by sales organization.
          </p>
        </div>
      </div>

      {!team.length ? (
        <div className="ex-card">
          <div className="ex-blank">
            <b>No sales team on record.</b>
            <p>
              Nobody currently holds an active sales role in this brand's
              organizations. People appear here as they are added to a team.
            </p>
          </div>
        </div>
      ) : (
        groups.map(g => (
          <div className="ex-section" key={g.name}>
            <h2>
              {g.name}
              <span>· {num(g.members.length)}{' '}
                {plural(g.members.length, 'person', 'people')}</span>
            </h2>
            <div className="ex-tablewrap">
              <table className="ex-table">
                <thead>
                  <tr>
                    <th>Name</th><th>Role</th><th>Email</th><th>On the team since</th>
                  </tr>
                </thead>
                <tbody>
                  {g.members.map(m => (
                    <tr key={m.user_id + m.brand_sales_org_id}>
                      <td className="ex-org">{m.name}</td>
                      <td>{ROLE_LABELS[m.role] || m.role}</td>
                      <td>{m.email}</td>
                      <td><Val value={day(m.joined)} absent="not recorded" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}

      <p className="ex-muted" style={{ marginTop: 18, maxWidth: '70ch', lineHeight: 1.7 }}>
        Individual production and commission are not shown here. Those figures
        live on the compensation surface, which is granted separately from an
        executive portfolio — showing a ranked table of people from data this
        view is not authorized to read would look authoritative and would not
        be.
      </p>
    </>
  )
}
