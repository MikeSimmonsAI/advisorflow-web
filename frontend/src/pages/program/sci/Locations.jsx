/* Locations: a searchable directory of every location and its profile - what
   families see. GET /program/locations; saving is PATCH /program/locations/{id}.
   Missing data stays missing ("Not provided"); nothing is invented. A location
   whose identity is unverified (verification_held, today Oaklawn) is shown as held. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { LANDSCAPE, Chip, Empty, ErrorLine, Field, Loading, PageHead, assetSrc, errText, when } from './ui'

const TABS = [['overview', 'Overview'], ['messaging', 'Messaging'], ['assets', 'Assets'], ['team', 'Team']]
const FIELDS = {
  overview: [['official_name', 'Official name'], ['facility_phone', 'Facility phone', 'Not shown to families'],
    ['website', 'Website'], ['appointment_link', 'Booking link'], ['address_line1', 'Address'], ['city', 'City'],
    ['state', 'State'], ['postal_code', 'ZIP']],
  messaging: [['email_alias', 'Location email address'], ['mailbox_folder', 'Outlook folder name'],
    ['email_display_name', 'Email sender name (override)', 'Leave blank for the default'],
    ['sms_identity_name', 'Text sign-off name (override)', 'Leave blank for the default']],
  team: [['manager_name', 'Manager']],
}
const COMPLETE_KEYS = ['address', 'facility_phone', 'appointment_link', 'manager_name', 'alias']

function completeness(p) {
  const have = {
    address: !!(p.address?.address_line1 && p.address?.city && p.address?.state),
    facility_phone: !!p.facility_phone, appointment_link: !!p.appointment_link,
    manager_name: !!p.manager_name, alias: !!p.alias_receiving,
  }
  const n = COMPLETE_KEYS.filter(k => have[k]).length
  return { n, total: COMPLETE_KEYS.length, pct: Math.round((n / COMPLETE_KEYS.length) * 100) }
}

function formOf(p) {
  return {
    official_name: p.official_name || '', website: p.website || '', facility_phone: p.facility_phone || '',
    manager_name: p.manager_name || '', address_line1: p.address?.address_line1 || '', city: p.address?.city || '',
    state: p.address?.state || '', postal_code: p.address?.postal_code || '', appointment_link: p.appointment_link || '',
    email_alias: p.email_alias || '', mailbox_folder: p.mailbox_folder || '',
    email_display_name: '', sms_identity_name: '', advisor_names: (p.advisor_names || []).join(', '),
    logo_asset_id: '', hero_asset_id: '',
  }
}

export default function Locations({ isManager, selected, onChange }) {
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [q, setQ] = useState('')
  const [tab, setTab] = useState('overview')
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [assets, setAssets] = useState([])

  const choose = useCallback(p => { setCur(p); setForm(formOf(p)); setMsg(''); setErr(''); setEditing(false) }, [])
  const load = useCallback(() => {
    api.get('/program/locations').then(d => {
      setRows(d)
      setCur(c => {
        const pick = (c && d.find(p => p.id === c.id)) || d.find(p => p.location_id === selected) || d[0]
        if (pick) setForm(formOf(pick))
        return pick || null
      })
    }).catch(e => setErr(errText(e)))
    api.get('/program/assets').then(d => setAssets(d.items || [])).catch(() => {})
  }, [selected])
  useEffect(() => { load() }, [load])

  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (rows || []).filter(p => !s || p.official_name.toLowerCase().includes(s)
      || (p.address?.city || '').toLowerCase().includes(s) || (p.source_names || []).some(n => n.toLowerCase().includes(s)))
  }, [rows, q])
  const real = (rows || []).filter(p => !p.is_review_bucket)

  const save = async () => {
    setErr(''); setMsg(''); setSaving(true)
    const body = {}
    Object.entries(form).forEach(([k, v]) => {
      if (k === 'advisor_names') body.advisor_names = v.split(',').map(s => s.trim()).filter(Boolean)
      else if (['email_display_name', 'sms_identity_name', 'logo_asset_id', 'hero_asset_id'].includes(k) && !v) return
      else body[k] = v
    })
    try {
      const p = await api.patch(`/program/locations/${cur.id}`, body)
      setRows(rs => rs.map(r => (r.id === p.id ? p : r))); choose(p); setMsg('Saved.'); onChange()
    } catch (e) { setErr(errText(e)) } finally { setSaving(false) }
  }

  if (!rows && !err) return <><PageHead title="Location directory" /><Loading /></>
  const imgs = assets.filter(a => a.kind !== 'flyer')
  const c = cur ? completeness(cur) : null

  return (
    <>
      <PageHead title="Location directory" sub={`Manage all ${real.length} locations from a single place.`} />
      <ErrorLine text={!cur ? err : ''} />
      <div className="sci-split">
        <section className="sci-panel sci-pad" aria-labelledby="sci-loclist">
          <h2 id="sci-loclist" className="sci-h2">{real.length} mapped locations</h2>
          <div className="sci-search" style={{ width: '100%', margin: '12px 0' }}>
            <label htmlFor="sci-loc-q" className="sci-sr">Search locations</label>
            <input id="sci-loc-q" value={q} onChange={e => setQ(e.target.value)} placeholder="Search locations or cities" />
          </div>
          <div className="sci-list" aria-label="Locations">
            {list.length === 0 && <Empty>No location matches.</Empty>}
            {list.map(p => {
              const pc = completeness(p)
              return (
                <button key={p.id} type="button" className="sci-listitem" aria-current={cur?.id === p.id} onClick={() => { choose(p); setTab('overview') }}>
                  <span className="sci-thumb" style={{ backgroundImage: `url(${p.hero_url ? assetSrc(p.hero_url) : LANDSCAPE})` }} aria-hidden="true" />
                  <span className="sci-listitem-body">
                    <b>{p.official_name}</b>
                    <small>
                      {p.is_review_bucket ? 'Contacts waiting for a location'
                        : p.verification_held ? 'Held — pending verification'
                          : pc.n === pc.total ? 'Profile complete' : `Profile needs details (${pc.n}/${pc.total})`}
                    </small>
                  </span>
                  {p.verification_held && <Chip tone="bad" plain>Held</Chip>}
                  {p.is_review_bucket && <Chip tone="warn" plain>Review</Chip>}
                </button>
              )
            })}
          </div>
        </section>

        {cur && (
          <section className="sci-panel" aria-labelledby="sci-locname">
            <div className="sci-locbanner" style={{ backgroundImage: `url(${cur.hero_url ? assetSrc(cur.hero_url) : LANDSCAPE})` }}>
              <div className="sci-locbanner-title">
                <h2 id="sci-locname">{cur.official_name}</h2>
                <span>{cur.is_review_bucket ? 'Location review bucket' : 'SCI location profile'}</span>
              </div>
            </div>
            <div className="sci-pad">
              {cur.is_review_bucket ? (
                <p className="sci-small">Contacts whose location could not be resolved wait here. Nothing in this bucket is ever sent to. Assign them a location from Review.</p>
              ) : (
                <>
                  {cur.verification_held && (
                    <div className="sci-banner bad"><div><strong>Held — pending verification</strong>This location's identity is not confirmed with SCI, so nothing is sent for its contacts.</div></div>
                  )}
                  <div className="sci-tabs" role="tablist" aria-label="Location profile">
                    {TABS.map(([k, label]) => (
                      <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{label}</button>
                    ))}
                  </div>
                  <ErrorLine text={err} />
                  {msg && <div className="sci-okmsg" role="status">{msg}</div>}

                  {tab === 'overview' && (editing ? <EditFields keys={FIELDS.overview} form={form} setForm={setForm} /> : (
                    <dl className="sci-cards" style={{ margin: 0 }}>
                      <Card label="Official name" value={cur.official_name} />
                      <Card label="Location email" value={cur.email_alias} />
                      <Card label="Facility phone" value={cur.facility_phone} />
                      <Card label="Address" value={[cur.address?.address_line1, cur.address?.city, cur.address?.state, cur.address?.postal_code].filter(Boolean).join(', ')} />
                      <Card label="Booking link" value={cur.appointment_link} />
                      <Card label="Manager" value={cur.manager_name} missing="Not assigned" />
                      <Card label="Website" value={cur.website} />
                      <Card label="Source names" value={(cur.source_names || []).join(', ')} />
                    </dl>
                  ))}

                  {tab === 'messaging' && (
                    <>
                      <dl className="sci-cards" style={{ margin: '0 0 14px' }}>
                        <Card label="Families see (email)" value={cur.email_display_name} />
                        <Card label="Text sign-off" value={cur.sms_signoff} />
                        <div className="sci-card"><dt>Location email status</dt><dd>
                          {cur.email_alias ? (
                            <Chip tone={cur.alias_receiving ? 'ok' : 'warn'}>
                              {cur.alias_receiving ? (cur.alias_mode === 'from' ? 'In use as From + Reply-To' : cur.alias_mode === 'reply_to' ? 'In use as Reply-To' : 'Receiving')
                                : 'Not seen receiving mail yet'}
                            </Chip>
                          ) : <span className="sci-muted">No address yet</span>}
                          {cur.alias_last_seen_at && <div className="sci-micro sci-muted" style={{ marginTop: 6 }}>Last seen {when(cur.alias_last_seen_at)}</div>}
                        </dd></div>
                      </dl>
                      {editing && <EditFields keys={FIELDS.messaging} form={form} setForm={setForm} />}
                    </>
                  )}

                  {tab === 'assets' && (
                    <>
                      <div className="sci-grid-even" style={{ marginBottom: 14 }}>
                        <div className="sci-card"><dt>Location logo</dt><dd>{cur.logo_url ? <img src={assetSrc(cur.logo_url)} alt={`${cur.official_name} logo`} style={{ maxHeight: 80, maxWidth: '100%' }} /> : <span className="missing" style={{ color: 'var(--warn)' }}>Not provided</span>}</dd></div>
                        <div className="sci-card"><dt>Facility image</dt><dd>{cur.hero_url ? <img src={assetSrc(cur.hero_url)} alt="" style={{ maxHeight: 120, maxWidth: '100%', borderRadius: 8 }} /> : <span style={{ color: 'var(--warn)' }}>Not provided — the landscape artwork is shown instead</span>}</dd></div>
                      </div>
                      {editing && (
                        <div className="sci-form">
                          <Field label="Location logo">
                            <select value={form.logo_asset_id} onChange={e => setForm(f => ({ ...f, logo_asset_id: e.target.value }))}>
                              <option value="">{cur.logo_url ? 'Keep current' : 'None'}</option>
                              {imgs.map(a => <option key={a.id} value={a.id}>{a.title} v{a.version}</option>)}
                            </select>
                          </Field>
                          <Field label="Facility image">
                            <select value={form.hero_asset_id} onChange={e => setForm(f => ({ ...f, hero_asset_id: e.target.value }))}>
                              <option value="">{cur.hero_url ? 'Keep current' : 'None'}</option>
                              {imgs.map(a => <option key={a.id} value={a.id}>{a.title} v{a.version}</option>)}
                            </select>
                          </Field>
                        </div>
                      )}
                      {!imgs.length && <p className="sci-micro sci-muted">No logos or facility images uploaded yet. Add them under Assets & Flyers.</p>}
                    </>
                  )}

                  {tab === 'team' && (editing ? (
                    <div className="sci-form">
                      <EditFields keys={FIELDS.team} form={form} setForm={setForm} bare />
                      <Field label="Advisors" hint="Comma separated" full>
                        <input value={form.advisor_names} onChange={e => setForm(f => ({ ...f, advisor_names: e.target.value }))} />
                      </Field>
                    </div>
                  ) : (
                    <dl className="sci-cards" style={{ margin: 0 }}>
                      <Card label="Manager" value={cur.manager_name} missing="Not assigned" />
                      <Card label="Advisors" value={(cur.advisor_names || []).join(', ')} missing="None listed" />
                    </dl>
                  ))}

                  <div style={{ marginTop: 22 }}>
                    <div className="sci-panel-head" style={{ marginBottom: 8 }}>
                      <h3 className="sci-h3" style={{ margin: 0 }}>Profile completeness</h3>
                      <Chip tone={c.n === c.total ? 'ok' : 'warn'}>{c.n === c.total ? 'Complete' : `${c.n} of ${c.total}`}</Chip>
                    </div>
                    <div className="sci-progress" role="progressbar" aria-valuenow={c.pct} aria-valuemin={0} aria-valuemax={100} aria-label="Profile completeness"><span style={{ width: `${c.pct}%` }} /></div>
                    <p className="sci-micro sci-muted">Address, facility phone, booking link, manager and a working location email. Missing details are left blank — never filled in for you.</p>
                  </div>

                  {isManager && (
                    <div className="sci-savebar">
                      {editing ? (
                        <>
                          <button type="button" className="sci-btn" onClick={() => { setForm(formOf(cur)); setEditing(false) }}>Cancel</button>
                          <button type="button" className="sci-btn primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save location'}</button>
                        </>
                      ) : <button type="button" className="sci-btn primary" onClick={() => setEditing(true)}>Edit profile</button>}
                    </div>
                  )}
                </>
              )}
            </div>
          </section>
        )}
      </div>
    </>
  )
}

function Card({ label, value, missing = 'Not provided' }) {
  return (
    <div className="sci-card">
      <dt>{label}</dt>
      <dd className={value ? '' : 'missing'}>{value || missing}</dd>
    </div>
  )
}

function EditFields({ keys, form, setForm, bare = false }) {
  const inputs = keys.map(([k, label, hint]) => (
    <Field key={k} label={label} hint={hint}>
      <input value={form[k] ?? ''} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
    </Field>
  ))
  return bare ? <>{inputs}</> : <div className="sci-form">{inputs}</div>
}
