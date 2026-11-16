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

function changeIcon(item) {
  if (item.kind === 'mention') return null
  return item.change === 'cancelled' ? 'calendarX' : item.change === 'updated' ? 'calendarEdit' : 'calendarCheck'
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
  const tileIcon = mode === 'updates' ? (changeIcon(item) || r.icon) : r.icon
  const end = endOf(item.starts_at, item.minutes)
  return (
    <li>
      <button type="button" className="ta-arow" style={{ '--acc': r.accent }} onClick={open || undefined} disabled={!open}
              data-kind={item.kind}>
        <span className="ta-prow-tile" aria-hidden><Icon name={tileIcon} size={22} /></span>
        <span className="ta-prow-main">
          {mode === 'updates' ? <>
            <span className="ta-prow-name">{r.name}</span>
            <span className="ta-arow-text">{item.text}</span>
            {item.kind === 'mention' && item.preview && <span className="ta-arow-sub">“{item.preview}”</span>}
            {item.kind !== 'mention' && item.starts_at && (
              <span className="ta-arow-sub">{item.title} · {fmtLocalDay(localYmd(item.starts_at, tz))} {fmtTime(item.starts_at, tz)}</span>)}
            <span className="ta-arow-when">{fmtListTime(item.at, tz, nowIso)}</span>
          </> : <>
            <span className="ta-prow-name">{r.name === 'Sales Workspace' ? item.title : r.name + ' ' + item.title}</span>
            <span className="ta-arow-line"><Icon name="calendar" size={16} /> {fmtLocalDay(localYmd(item.starts_at, tz))}</span>
            <span className="ta-arow-line"><Icon name="clock" size={16} /> {fmtTime(item.starts_at, tz)}{end ? ' – ' + fmtTime(end, tz) : ''}
              {item.where ? ' · ' + item.where : ''}</span>
          </>}
        </span>
        {open && <Icon name="chev" size={20} />}
      </button>
    </li>
  )
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
      : <ul className="ta-plist" aria-label="Updates">{data.items.map(i => <Row key={i.id} item={i} inv={inv} tz={tz} mode="updates" nowIso={nowIso} />)}</ul>}
  </>
}

function Upcoming({ inv, tz }) {
  const today = localYmd(new Date().toISOString(), tz)
  const [day, setDay] = useState(today)
  const { data, error, loading, reload } = useTeamApi('/team-app/activity/upcoming?date=' + day)
  return <>
    <div className="ta-daynav">
      <button type="button" className="ta-daybtn" onClick={() => setDay(d => addDays(d, -1))} aria-label="Previous day"><Icon name="back" size={20} /></button>
      <button type="button" className="ta-daylabel" onClick={() => setDay(today)} aria-label={'Showing ' + fmtLocalDay(day) + '. Tap for today.'}>
        <Icon name="calendar" size={20} /> {fmtLocalDay(day)}{day === today ? ' · Today' : ''}</button>
      <button type="button" className="ta-daybtn ta-flip" onClick={() => setDay(d => addDays(d, 1))} aria-label="Next day"><Icon name="back" size={20} /></button>
    </div>
    {loading && !data && <Loading />}
    {error && <ErrorNote error={error} onRetry={reload} />}
    {data && data.date === day && <>
      <Unavailable sources={data.sources} />
      {data.items.length === 0
        ? <div className="ta-state">Nothing scheduled for {fmtLocalDay(day)}.</div>
        : <ul className="ta-plist" aria-label={'Upcoming on ' + fmtLocalDay(day)}>{data.items.map(i => <Row key={i.id} item={i} inv={inv} tz={tz} mode="upcoming" />)}</ul>}
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
