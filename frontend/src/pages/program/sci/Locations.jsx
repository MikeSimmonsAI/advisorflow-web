/* Locations: a searchable master/detail directory of the real, mapped
   facilities and their profile - what families see. GET /program/locations;
   saving is PATCH /program/locations/{id} (manager only; the server enforces
   the locked customer-facing identity and the alias domain).
   Missing data stays missing ("Not provided"); nothing is invented. A
   location whose identity is unverified (verification_held - today Oaklawn)
   is shown as held. The "Location review" bucket is listed apart from the
   facilities: it is where contacts with no location wait, not a 40th site. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import {
  LANDSCAPE, Chip, Empty, ErrorLine, Field, FilterChips, Icon, KV, Kpi, Loading, Notice, PageHead, Panel,
  SearchBox, Tabs, assetSrc, errText, num, when,
} from './ui'

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
  return { n, total: COMPLETE_KEYS.length, pct: Math.round((n / COMPLETE_KEYS.length) * 100), have }
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

const addressOf = p => [p.address?.address_line1, p.address?.city, p.address?.state, p.address?.postal_code].filter(Boolean).join(', ')

export default function Locations({ isManager, selected, onChange, setParam, performance, goTab }) {
  const [rows, setRows] = useState(null)
  const [cur, setCur] = useState(null)
  const [q, setQ] = useState('')
  const [view, setView] = useState('all')
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
        const pick = (c && d.find(p => p.id === c.id)) || d.find(p => p.location_id === selected) || d.find(p => !p.is_review_bucket) || d[0]
        if (pick) setForm(formOf(pick))
        return pick || null
      })
    }).catch(e => setErr(errText(e)))
    api.get('/program/assets').then(d => setAssets(d.items || [])).catch(() => {})
  }, [selected])
  useEffect(() => { load() }, [load])

  const perf = useMemo(() => Object.fromEntries((performance || []).map(r => [r.location_id, r])), [performance])
  const real = (rows || []).filter(p => !p.is_review_bucket)
  const bucket = (rows || []).find(p => p.is_review_bucket)
  const held = real.filter(p => p.verification_held)
  const incomplete = real.filter(p => { const c = completeness(p); return c.n < c.total })
  const list = useMemo(() => {
    const s = q.trim().toLowerCase()
    return real.filter(p => (view === 'all' || (view === 'held' ? p.verification_held : completeness(p).n < completeness(p).total))
      && (!s || p.official_name.toLowerCase().includes(s) || (p.address?.city || '').toLowerCase().includes(s)
        || (p.source_names || []).some(n => n.toLowerCase().includes(s))))
  }, [real, q, view])
  const leadsTotal = real.reduce((s, p) => s + (perf[p.location_id]?.leads || 0), 0)

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

  if (!rows && !err) return <><PageHead title="Locations" /><Loading /></>
  const imgs = assets.filter(a => a.kind !== 'flyer')
  const c = cur && !cur.is_review_bucket ? completeness(cur) : null
  const pc = cur ? perf[cur.location_id] : null

  return (
    <>
      <PageHead title="Locations" sub={`Manage identity, readiness and messaging across ${real.length} SCI facilities.`}>
        <Chip tone="info">{real.length} mapped locations</Chip>
        {held.length > 0 && <Chip tone="bad">{held.length} held</Chip>}
      </PageHead>

      <section className="sci-kpis four" aria-label="Location numbers">
        <Kpi icon="pin" tone="blue" value={real.length} label="Mapped locations" note="Real facilities" onClick={() => setView('all')} />
        <Kpi icon="users" tone="green" value={leadsTotal} label="Leads distributed" note={bucket && perf[bucket.location_id]?.leads ? `+${num(perf[bucket.location_id].leads)} waiting for a location` : 'Across facilities'} />
        <Kpi icon="alert" tone="gold" value={incomplete.length} label="Profiles need details" note="Address, phone, booking, manager, email" onClick={() => setView('incomplete')} />
        <Kpi icon="lock" tone="red" value={held.length} label="Held pending verification" note="Nothing sent for these" onClick={() => setView('held')} />
      </section>
      <ErrorLine text={!cur ? err : ''} />

      <div className="sci-split">
        <Panel title="Location directory" labelledBy="sci-loclist" aside={<span className="sci-micro sci-muted">{list.length} of {real.length}</span>}>
          <div style={{ marginBottom: 8 }}><SearchBox id="sci-loc-q" value={q} onChange={setQ} placeholder="Search facilities or cities" label="Search locations" width="100%" /></div>
          <div style={{ marginBottom: 8 }}>
            <FilterChips items={[['all', 'All'], ['incomplete', 'Needs details', incomplete.length], ['held', 'Held', held.length]]} value={view} onChange={setView} label="Filter locations" />
          </div>
          <div className="sci-list" aria-label="Locations">
            {list.length === 0 && <Empty icon="pin">No location matches.</Empty>}
            {list.map(p => {
              const lc = completeness(p)
              const pr = perf[p.location_id]
              return (
                <button key={p.id} type="button" className="sci-listitem" aria-current={cur?.id === p.id} onClick={() => { choose(p); setTab('overview') }}>
                  <span className="sci-listitem-body">
                    <b>{p.official_name}</b>
                    <small>{pr ? `${num(pr.leads)} leads · ${num(pr.qualified)} qualified` : 'No contacts yet'} · {p.verification_held ? 'held' : lc.n === lc.total ? 'complete' : `${lc.n}/${lc.total} details`}</small>
                  </span>
                  {p.verification_held ? <Chip tone="bad" plain>Held</Chip> : lc.n === lc.total ? <Chip tone="ok" plain>Ready</Chip> : null}
                  <span className="sci-chev" aria-hidden="true"><Icon name="chevron" /></span>
                </button>
              )
            })}
            {bucket && view === 'all' && !q && (
              <>
                <div className="sci-listsep">Not a facility</div>
                <button type="button" className="sci-listitem" aria-current={cur?.id === bucket.id} onClick={() => choose(bucket)}>
                  <span className="sci-listitem-body">
                    <b>{bucket.official_name}</b>
                    <small>{num(perf[bucket.location_id]?.leads || 0)} contacts waiting for a location</small>
                  </span>
                  <Chip tone="warn" plain>Review</Chip>
                </button>
              </>
            )}
          </div>
        </Panel>

        {cur && (
          <section className="sci-panel" aria-labelledby="sci-locname">
            <div className="sci-locbanner" style={{ backgroundImage: `url(${cur.hero_url ? assetSrc(cur.hero_url) : LANDSCAPE})` }}>
              <div className="sci-locbanner-title">
                <div>
                  <h2 id="sci-locname">{cur.official_name}</h2>
                  <span>{cur.is_review_bucket ? 'Location review bucket — not a facility' : addressOf(cur) || 'Address not provided'}</span>
                </div>
                {!cur.is_review_bucket && (
                  <div className="sci-row-gap">
                    {cur.verification_held ? <Chip tone="bad">Held — pending verification</Chip>
                      : c.n === c.total ? <Chip tone="ok">Profile complete</Chip> : <Chip tone="warn">{c.n} of {c.total} details</Chip>}
                  </div>
                )}
              </div>
            </div>
            <div className="sci-pad">
              {cur.is_review_bucket ? (
                <>
                  <p className="sci-small">Contacts whose location could not be resolved wait here. Nothing in this bucket is ever sent to. Assign each contact a real location from Review.</p>
                  <button type="button" className="sci-btn primary" style={{ marginTop: 10 }} onClick={() => goTab('review', { queue: 'location_review' })}>Open location issues in Review</button>
                </>
              ) : (
                <>
                  {cur.verification_held && (
                    <div className="sci-banner bad"><span className="sci-banner-icon" aria-hidden="true"><Icon name="lock" /></span>
                      <div className="sci-banner-body"><strong>Held — pending verification</strong>This location's identity is not confirmed with SCI, so nothing is sent for its contacts.</div></div>
                  )}
                  {pc && (
                    <div className="sci-statstrip" style={{ marginBottom: 12 }}>
                      <div className="sci-stat"><b>{num(pc.leads)}</b><span>Leads</span></div>
                      <div className="sci-stat"><b>{num(pc.qualified)}</b><span>Qualified</span></div>
                      <div className="sci-stat"><b>{num(pc.new_responses)}</b><span>New replies</span></div>
                      <div className="sci-stat"><b>{num(pc.hot)}</b><span>HOT open</span></div>
                      <div className="sci-stat"><b>{pc.response_rate == null ? '—' : `${pc.response_rate}%`}</b><span>Response rate</span></div>
                    </div>
                  )}
                  <Tabs items={TABS} value={tab} onChange={setTab} label="Location profile" />
                  <ErrorLine text={err} />
                  <Notice text={msg} onClose={() => setMsg('')} />

                  {tab === 'overview' && (editing ? <EditFields keys={FIELDS.overview} form={form} setForm={setForm} /> : (
                    <>
                      <KV rows={[
                        ['Official name', cur.official_name], ['Facility phone', cur.facility_phone],
                        ['Address', addressOf(cur)], ['Booking link', cur.appointment_link],
                        ['Location email', cur.email_alias], ['Manager', cur.manager_name],
                        ['Website', cur.website], ['Source names', (cur.source_names || []).join(', ')],
                      ]} />
                      <h3 className="sci-h3" style={{ marginTop: 14 }}>Location-specific sender identity</h3>
                      <div className="sci-identity">
                        <span className="sci-identity-mark">SCI</span>
                        <div style={{ minWidth: 0 }}>
                          <b>{cur.email_display_name || 'Not provided'}</b>
                          <div className="sci-micro sci-muted">Email "from" name · text sign-off: {cur.sms_signoff || 'Not provided'}</div>
                        </div>
                      </div>
                    </>
                  ))}

                  {tab === 'messaging' && (
                    <>
                      <KV rows={[
                        ['Families see (email)', cur.email_display_name], ['Text sign-off', cur.sms_signoff],
                        ['Location email', cur.email_alias], ['Outlook folder', cur.mailbox_folder],
                      ]} />
                      <div className="sci-card" style={{ marginTop: 10 }}>
                        <dt>Location email status</dt>
                        <dd>
                          {cur.email_alias ? (
                            <Chip tone={cur.alias_receiving ? 'ok' : 'warn'}>
                              {cur.alias_receiving ? (cur.alias_mode === 'from' ? 'In use as From + Reply-To' : cur.alias_mode === 'reply_to' ? 'In use as Reply-To' : 'Receiving')
                                : 'Not seen receiving mail yet'}
                            </Chip>
                          ) : <span className="sci-muted">No address yet</span>}
                          {cur.alias_last_seen_at && <span className="sci-micro sci-muted" style={{ marginLeft: 8 }}>Last seen {when(cur.alias_last_seen_at)}</span>}
                        </dd>
                      </div>
                      {editing && <div style={{ marginTop: 12 }}><EditFields keys={FIELDS.messaging} form={form} setForm={setForm} /></div>}
                    </>
                  )}

                  {tab === 'assets' && (
                    <>
                      <div className="sci-grid-even" style={{ marginBottom: 12 }}>
                        <div className="sci-card"><dt>Location logo</dt><dd>{cur.logo_url ? <img src={assetSrc(cur.logo_url)} alt={`${cur.official_name} logo`} style={{ maxHeight: 70, maxWidth: '100%' }} /> : <span className="sci-missing">Not provided</span>}</dd></div>
                        <div className="sci-card"><dt>Facility image</dt><dd>{cur.hero_url ? <img src={assetSrc(cur.hero_url)} alt="" style={{ maxHeight: 100, maxWidth: '100%', borderRadius: 8 }} /> : <span className="sci-missing">Not provided — landscape artwork is shown instead</span>}</dd></div>
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
                      {!imgs.length && <p className="sci-micro sci-muted">No logos or facility images uploaded yet. <button type="button" className="sci-link" onClick={() => goTab('assets')}>Add them under Assets & Flyers.</button></p>}
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
                    <KV rows={[['Manager', cur.manager_name], ['Advisors', (cur.advisor_names || []).join(', ')]]} />
                  ))}

                  <div style={{ marginTop: 16 }}>
                    <div className="sci-panel-head" style={{ marginBottom: 6 }}>
                      <h3 className="sci-h3" style={{ margin: 0 }}>Profile completeness</h3>
                      <span className="sci-micro sci-muted">{c.n} of {c.total}</span>
                    </div>
                    <div className="sci-progress" role="progressbar" aria-valuenow={c.pct} aria-valuemin={0} aria-valuemax={100} aria-label="Profile completeness"><span style={{ width: `${c.pct}%` }} /></div>
                    <div className="sci-row-gap" style={{ marginTop: 8 }}>
                      {[['address', 'Address'], ['facility_phone', 'Phone'], ['appointment_link', 'Booking link'], ['manager_name', 'Manager'], ['alias', 'Working email']].map(([k, l]) => (
                        <Chip key={k} tone={c.have[k] ? 'ok' : 'warn'} plain>{c.have[k] ? '✓' : '•'} {l}</Chip>
                      ))}
                    </div>
                  </div>

                  <div className="sci-savebar" style={{ justifyContent: 'space-between' }}>
                    <div className="sci-row-gap">
                      <button type="button" className="sci-btn sm" onClick={() => setParam('location', cur.location_id)} disabled={selected === cur.location_id}>
                        <Icon name="filter" />{selected === cur.location_id ? 'Workspace filtered to this location' : 'Filter workspace to this location'}
                      </button>
                      <button type="button" className="sci-btn sm" onClick={() => goTab('send', { location: cur.location_id })}><Icon name="users" />View contacts</button>
                    </div>
                    {isManager ? (
                      <div className="sci-row-gap">
                        {editing ? (
                          <>
                            <button type="button" className="sci-btn" onClick={() => { setForm(formOf(cur)); setEditing(false) }}>Cancel</button>
                            <button type="button" className="sci-btn primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save location'}</button>
                          </>
                        ) : <button type="button" className="sci-btn gold" onClick={() => setEditing(true)}><Icon name="edit" />Edit profile</button>}
                      </div>
                    ) : <span className="sci-micro sci-muted">Only a workspace manager can edit profiles.</span>}
                  </div>
                </>
              )}
            </div>
          </section>
        )}
      </div>
    </>
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
