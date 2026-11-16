/**
 * ACTIVITY — Updates (what changed) and Upcoming (a day's agenda).
 *
 * Everything comes from /team-app/activity, which reads existing records and
 * applies each source's own access rule on the server. This screen only draws
 * it: an item takes the colour, icon and name of the Program Access entry it
 * belongs to (the same accents as Programs), and opening it uses that entry's
 * own destination, which re-checks access again. A source the server reports
 * as unavailable is named plainly instead of silently missing.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchMyContexts } from '../api/client'
import { programInventory } from '../pages/program/programAccessModel'
import { openProgramItem } from '../pages/program/programDestinations'
import { MOBILE_PROGRAMS } from '../pages/program/launcherOrigin'
import { ACCENTS, addDays, fmtListTime, fmtLocalDay, fmtTime, LABELS, localYmd, programStyle } from './teamHelpers'
import { ErrorNote, Icon, Loading, ScreenTitle, useTeam, useTeamApi } from './TeamApp'

const SOURCE_NAMES = {
  workspace_bookings: 'Workspace appointments',
  sales_appointments: 'Sales appointments',
  chat_mentions: 'Chat mentions',
}

function useInventory() {
  const [inv, setInv] = useState(null)
  useEffect(() => {
    let live = true
    fetchMyContexts().then(c => { if (live) setInv(programInventory(c || {}, (c || {}).deal_builder, MOBILE_PROGRAMS)) })
      .catch(() => { if (live) setInv([]) })
    return () => { live = false }
  }, [])
  return inv
}

/** Look, label and destination for one activity item. */
function resolve(item, inv) {
  if (item.kind === 'mention') {
    const icon = item.room_kind === 'announcements' ? 'megaphone' : item.room_kind === 'room' ? 'team' : 'chat'
    return { accent: ACCENTS.sales, icon, name: item.headline, to: '/app/chat/' + item.room_id }
  }
  if (item.kind === 'sales_appointment') {
    const card = (inv || []).find(c => c.type === 'back_office')
    return { accent: ACCENTS.sales, icon: 'briefcase', name: 'Sales Workspace', card }
  }
  const card = (inv || []).find(c => c.organization_id === item.organization_id && (c.type || 'workspace') === 'workspace')
  const look = programStyle(card || { display: item.workspace })
  return { accent: look.accent, icon: look.icon, name: card ? card.display : (item.headline || item.workspace), card }
}

function endOf(startWire, minutes) {
  if (!startWire || !minutes) return null
  const d = new Date(startWire)
  if (Number.isNaN(d.getTime())) return null
  return new Date(d.getTime() + minutes * 60000).toISOString()
}

function Row({ item, inv, tz, mode, nowIso }) {
  const navigate = useNavigate()
  const r = resolve(item, inv)
  const open = r.to ? () => navigate(r.to) : r.card ? () => openProgramItem(r.card, navigate, { origin: MOBILE_PROGRAMS }) : null
  const end = endOf(item.starts_at, item.minutes)
  const style = { '--acc': r.accent }
  return (
    <li>
      <button type="button" className="ta-acard" style={style} onClick={open || undefined} disabled={!open}
              data-kind={item.kind} data-change={item.change || ''}>
        <span className="ta-acard-tile" aria-hidden><Icon name={r.icon} size={24} /></span>
        <span className="ta-prow-main">
          {mode === 'updates' ? <>
            <span className="ta-acard-title">{r.name} · {String(item.text || '').replace(/\.$/, '')}</span>
            {item.kind === 'mention' && item.preview && <span className="ta-arow-sub">“{item.preview}”</span>}
            {item.kind !== 'mention' && item.starts_at && (
              <span className="ta-arow-sub">{item.title} · {fmtLocalDay(localYmd(item.starts_at, tz))}, {fmtTime(item.starts_at, tz)}</span>)}
            <span className="ta-arow-when">{fmtListTime(item.at, tz, nowIso)}</span>
          </> : <>
            <span className="ta-acard-title">{r.name === 'Sales Workspace' ? item.title : r.name + ' ' + item.title}</span>
            <span className="ta-acard-time">{fmtTime(item.starts_at, tz)}{end ? ' – ' + fmtTime(end, tz) : ''}</span>
            {item.where && <span className="ta-arow-sub">{item.where}</span>}
          </>}
        </span>
        {open && <Icon name="chev" size={20} />}
      </button>
    </li>
  )
}

/** Group update items by the local day they happened: Today, Yesterday, or the date. */
function groupByDay(items, tz) {
  const today = localYmd(new Date().toISOString(), tz)
  const yesterday = addDays(today, -1)
  const out = []
  for (const it of items) {
    const d = localYmd(it.at, tz)
    const label = d === today ? 'Today' : d === yesterday ? 'Yesterday' : fmtLocalDay(d)
    const last = out[out.length - 1]
    if (last && last.label === label) last.items.push(it); else out.push({ label, items: [it] })
  }
  return out
}

function Unavailable({ sources }) {
  const down = (sources || []).filter(s => !s.available)
  if (!down.length) return null
  return (
    <p className="ta-note" role="status">
      Not available right now: {down.map(s => SOURCE_NAMES[s.key] || s.key).join(', ')}. Everything else is up to date.
    </p>
  )
}

function Updates({ inv, tz }) {
  const { data, error, loading, reload } = useTeamApi('/team-app/activity/updates', { pollMs: 30000 })
  if (loading && !data) return <Loading />
  if (error && !data) return <ErrorNote error={error} onRetry={reload} />
  const nowIso = new Date().toISOString()
  return <>
    <Unavailable sources={data.sources} />
    {data.items.length === 0
      ? <div className="ta-state">Nothing new in the last {data.days} days.<br />Bookings, appointment changes{data.chat ? ' and chat mentions' : ''} show up here.</div>
      : groupByDay(data.items, tz).map(g => (
        <section key={g.label} className="ta-section">
          <h2 className="ta-h2">{g.label}</h2>
          <ul className="ta-cards" aria-label={'Updates, ' + g.label}>{g.items.map(i => <Row key={i.id} item={i} inv={inv} tz={tz} mode="updates" nowIso={nowIso} />)}</ul>
        </section>))}
  </>
}

function Upcoming({ inv, tz }) {
  const today = localYmd(new Date().toISOString(), tz)
  const [day, setDay] = useState(today)
  const { data, error, loading, reload } = useTeamApi('/team-app/activity/upcoming?date=' + day)
  const heading = day === today ? 'Today' : day === addDays(today, 1) ? 'Tomorrow' : day === addDays(today, -1) ? 'Yesterday' : fmtLocalDay(day)
  return <>
    <div className="ta-daynav">
      <button type="button" className="ta-daybtn" onClick={() => setDay(d => addDays(d, -1))} aria-label="Previous day"><Icon name="back" size={20} /></button>
      <label className="ta-daylabel">
        <span>{fmtLocalDay(day)}</span><Icon name="chevDown" size={18} />
        <input type="date" value={day} aria-label="Choose a day" onChange={e => { if (e.target.value) setDay(e.target.value) }} />
      </label>
      <button type="button" className="ta-daybtn ta-flip" onClick={() => setDay(d => addDays(d, 1))} aria-label="Next day"><Icon name="back" size={20} /></button>
    </div>
    <div className="ta-section-head">
      <h2 className="ta-h2">{heading}</h2>
      {day !== today && <button type="button" className="ta-linkbtn" onClick={() => setDay(today)}>Back to today</button>}
    </div>
    {loading && !data && <Loading />}
    {error && <ErrorNote error={error} onRetry={reload} />}
    {data && data.date === day && <>
      <Unavailable sources={data.sources} />
      {data.items.length === 0
        ? <div className="ta-empty"><Icon name="calendar" size={30} /><p>Nothing scheduled for {fmtLocalDay(day)}.</p></div>
        : <ul className="ta-cards" aria-label={'Upcoming on ' + fmtLocalDay(day)}>{data.items.map(i => <Row key={i.id} item={i} inv={inv} tz={tz} mode="upcoming" />)}</ul>}
    </>}
  </>
}

export default function ActivityScreen() {
  const { tz } = useTeam()
  const inv = useInventory()
  const [tab, setTab] = useState('updates')
  const tabs = useMemo(() => [['updates', LABELS.updates], ['upcoming', LABELS.upcoming]], [])
  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.activity} />
      <div className="ta-seg" role="tablist" aria-label="Activity view">
        {tabs.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''}
                  onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === 'updates' ? <Updates inv={inv} tz={tz} /> : <Upcoming inv={inv} tz={tz} />}
    </div>
  )
}
