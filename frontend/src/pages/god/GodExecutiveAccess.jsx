/**
 * GodExecutiveAccess — who oversees which customers, and who decided that.
 *
 * ===========================================================================
 * WHY THIS SCREEN EXISTS
 * ===========================================================================
 *
 * Executive visibility used to be brand-wide: holding an executive grant on a
 * brand exposed every organization on it, and the grant record had no
 * organization dimension at all. "Assign this person Restland only" was not
 * unimplemented — it was inexpressible, and there was no screen anywhere that
 * managed executive access.
 *
 * The authority is now explicit and per-organization. This is where an owner
 * sets it.
 *
 * ===========================================================================
 * TWO GRANTS, TWO QUESTIONS, AND THE SCREEN SAYS SO
 * ===========================================================================
 *
 *     BRAND GRANT        may this person open this brand's Executive Suite
 *     ORGANIZATION       which customers inside it may they actually see
 *
 * Both are required and neither implies the other. Ticking six customers for
 * somebody with no brand grant achieves nothing, so the screen states that
 * plainly instead of letting an owner think they have set somebody up.
 *
 * THE LIST IS THE WHOLE BRAND, not just what is already ticked. A checklist
 * that only showed current assignments could never be used to add one — which
 * sounds obvious and is exactly the trap a "current portfolio" view falls
 * into.
 *
 * ===========================================================================
 * EVERY CHANGE IS IMMEDIATE, AND NONE OF THEM DELETE ANYTHING
 * ===========================================================================
 *
 * Visibility is recomputed from the assignment rows on every request, so
 * removing a customer takes effect on that executive's next page load — there
 * is no cache to wait for. Removal DEACTIVATES the row rather than deleting
 * it, so who was given what, by whom and when survives the removal. That is
 * the record an audit actually needs.
 */

import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { SectionLabel, StatusBadge } from './StatusBadge'

export default function GodExecutiveAccess() {
  const [list, setList] = useState(null)
  const [listErr, setListErr] = useState('')
  const [selected, setSelected] = useState(null)   // {user_id, platform_id}
  const [detail, setDetail] = useState(null)
  const [detailErr, setDetailErr] = useState('')
  const [busy, setBusy] = useState('')

  const loadList = useCallback(() => {
    api.get('/executive/admin/executives')
      .then(r => { setList(r); setListErr('') })
      .catch(e => setListErr(e?.detail || e?.message || 'Could not load executives.'))
  }, [])

  useEffect(() => { loadList() }, [loadList])

  const loadDetail = useCallback((userId, platformId) => {
    setDetail(null); setDetailErr('')
    api.get(`/executive/admin/portfolio/${userId}?platform_id=${encodeURIComponent(platformId)}`)
      .then(r => setDetail(r))
      .catch(e => setDetailErr(e?.detail || e?.message || 'Could not load this portfolio.'))
  }, [])

  function open(row) {
    setSelected({ user_id: row.user_id, platform_id: row.platform_id })
    loadDetail(row.user_id, row.platform_id)
  }

  async function toggle(org) {
    if (!selected || busy) return
    setBusy(org.id)
    const url = org.assigned
      ? '/executive/admin/portfolio/unassign'
      : '/executive/admin/portfolio/assign'
    try {
      await api.post(url, {
        user_id: selected.user_id,
        organization_id: org.id,
      })
      // Re-read rather than flipping local state: the server is the authority
      // on what this person can see, and a checkbox that disagreed with it
      // would be the most dangerous kind of wrong on this screen.
      loadDetail(selected.user_id, selected.platform_id)
      loadList()
    } catch (e) {
      setDetailErr(e?.detail || e?.message || 'That change did not save.')
    } finally {
      setBusy('')
    }
  }

  return (
    <div style={{ padding: '4px 0 40px' }}>
      <SectionLabel note="· an executive sees only the customers assigned to them">
        EXECUTIVE ACCESS
      </SectionLabel>

      {listErr ? (
        <div className="gm-card gm-empty" style={{ color: '#ff8299' }}>{listErr}</div>
      ) : null}

      {!list && !listErr ? (
        <div className="gm-card gm-empty">Reading executive grants…</div>
      ) : null}

      {list && !list.executives.length ? (
        <div className="gm-card gm-empty">
          <p style={{ margin: 0, lineHeight: 1.7 }}>
            Nobody currently holds an executive grant on any brand.
            <br />
            An executive grant is what opens a brand's Executive Suite; the
            customers they see inside it are assigned here afterwards.
          </p>
        </div>
      ) : null}

      {list && list.executives.length ? (
        <div className="gm-card" style={{ overflow: 'hidden' }}>
          <table className="gm-table">
            <thead>
              <tr>
                <th>Executive</th>
                <th>Brand</th>
                <th style={{ textAlign: 'right' }}>Customers assigned</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.executives.map(row => (
                <tr key={row.user_id + row.platform_id}>
                  <td>
                    <b style={{ color: '#eaf4ff' }}>{row.name}</b>
                    <span style={{ display: 'block', fontSize: 9.5, color: '#5e7796' }}>
                      {row.email}
                    </span>
                  </td>
                  <td>{row.platform_name}</td>
                  <td style={{ textAlign: 'right' }}>
                    {/* A GRANT WITH NO ASSIGNMENTS IS THE STATE WORTH SPOTTING.
                        Under the old model it meant "everything"; it now means
                        this person can sign in and see an empty portfolio. */}
                    {row.assigned_organizations === 0
                      ? <StatusBadge tone="warn">NONE ASSIGNED</StatusBadge>
                      : <b>{row.assigned_organizations}</b>}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="gm-btn" onClick={() => open(row)}>
                      Manage
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {selected ? (
        <div style={{ marginTop: 22 }}>
          <SectionLabel note="· tick a customer to put it in this executive's portfolio">
            PORTFOLIO
          </SectionLabel>

          {detailErr ? (
            <div className="gm-card gm-empty" style={{ color: '#ff8299' }}>
              {detailErr}
            </div>
          ) : null}

          {!detail && !detailErr ? (
            <div className="gm-card gm-empty">Loading…</div>
          ) : null}

          {detail ? (
            <div className="gm-card" style={{ padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                            alignItems: 'flex-start', gap: 16, flexWrap: 'wrap',
                            marginBottom: 14 }}>
                <div>
                  <b style={{ fontSize: 13, color: '#eaf4ff' }}>{detail.user.name}</b>
                  <span style={{ display: 'block', fontSize: 10, color: '#5e7796' }}>
                    {detail.user.email} · {detail.platform.name}
                  </span>
                </div>
                <button className="gm-btn" onClick={() => { setSelected(null); setDetail(null) }}>
                  Close
                </button>
              </div>

              {/* WITHOUT THE BRAND GRANT, TICKING BOXES ACHIEVES NOTHING. */}
              {!detail.has_brand_grant ? (
                <div style={{ border: '1px solid rgba(114,49,66,.9)',
                              background: '#2a1017', borderRadius: 10,
                              padding: '12px 14px', marginBottom: 14 }}>
                  <b style={{ fontSize: 11, color: '#ff829b', display: 'block' }}>
                    NO EXECUTIVE GRANT ON THIS BRAND
                  </b>
                  <p style={{ margin: '5px 0 0', fontSize: 10, color: '#a9c0d6',
                              lineHeight: 1.6 }}>
                    Assignments made here are stored, but this person cannot
                    open {detail.platform.name}'s Executive Suite until they are
                    granted the brand. Both are required and neither implies the
                    other.
                  </p>
                </div>
              ) : null}

              <p style={{ margin: '0 0 12px', fontSize: 10, color: '#68829f',
                          lineHeight: 1.65, maxWidth: '78ch' }}>
                {detail.assigned_count} of {detail.organizations.length}{' '}
                {detail.organizations.length === 1 ? 'customer' : 'customers'} on
                this brand. Changes take effect on their next page load, and
                removing a customer keeps the record that the access existed.
              </p>

              <div style={{ display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))',
                            gap: 8 }}>
                {detail.organizations.map(org => (
                  <label key={org.id}
                         style={{ display: 'flex', alignItems: 'center', gap: 10,
                                  padding: '10px 12px', borderRadius: 9,
                                  cursor: busy ? 'default' : 'pointer',
                                  border: '1px solid ' + (org.assigned
                                    ? 'rgba(23,111,88,.85)' : 'rgba(27,58,90,.9)'),
                                  background: org.assigned
                                    ? 'rgba(10,43,34,.5)' : 'rgba(10,26,46,.6)',
                                  opacity: busy === org.id ? 0.5 : 1 }}>
                    <input type="checkbox" checked={org.assigned}
                           disabled={!!busy}
                           onChange={() => toggle(org)} />
                    <span style={{ minWidth: 0 }}>
                      <b style={{ fontSize: 11.5, color: '#eaf4ff', display: 'block' }}>
                        {org.name}
                      </b>
                      {!org.is_active ? (
                        <span style={{ fontSize: 9, color: '#f4c652' }}>suspended</span>
                      ) : null}
                    </span>
                  </label>
                ))}
              </div>

              {!detail.organizations.length ? (
                <p style={{ fontSize: 11, color: '#68829f', margin: 0 }}>
                  This brand has no customer organizations yet, so there is
                  nothing to assign.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
