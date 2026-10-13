/* Outbox: every text and email that went to a family today — when, to whom,
   which channel, what it said and whether it was delivered — and who is
   scheduled next. GET /program/outbox?day=YYYY-MM-DD. Refreshes every minute. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, FilterChips, Icon, Kpi, Loading, PageHead, Panel, errText } from './ui'

const timeOf = iso => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—')
const STATUS = {
  delivered: ['Delivered', 'ok'], sent: ['Sent', 'ok'], opened: ['Opened', 'ok'], queued: ['Sending', ''],
  pending: ['Sent', ''], failed: ['Failed', 'bad'], undelivered: ['Not delivered', 'bad'], bounced: ['Bounced', 'bad'],
}
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export default function Outbox({ openPerson }) {
  const [day, setDay] = useState(ymd(new Date()))
  const [show, setShow] = useState('all')
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    api.get(`/program/outbox?day=${day}`).then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e)))
  }, [day])
  useEffect(() => { setD(null); load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])

  const sent = (d?.sent || []).filter(s => show === 'all' || s.channel === show)
  const isToday = day === ymd(new Date())
  return (
    <>
      <PageHead title="Outbox" sub="Every text and email sent to families, and who is scheduled next.">
        <input type="date" className="sci-input sm" value={day} onChange={e => e.target.value && setDay(e.target.value)} aria-label="Day" />
      </PageHead>
      <ErrorLine text={err} />
      {!d ? <Loading /> : (
        <>
          <section className="sci-kpis four" aria-label="Outbox summary">
            <Kpi icon="sms" tone="blue" value={d.texts_sent} label="Texts sent" />
            <Kpi icon="mail" tone="green" value={d.emails_sent} label="Emails sent" />
            <Kpi icon="alert" tone={d.failed ? 'red' : 'slate'} value={d.failed} label="Failed" />
            <Kpi icon="clock" tone="gold" value={d.coming_up} label={isToday ? 'Still scheduled today' : 'Scheduled'} />
          </section>

          {d.upcoming.length > 0 && (
            <Panel title="Coming up" labelledBy="sci-ob-next" aside={<span className="sci-micro sci-muted">{d.note}</span>}>
              <div className="sci-tablewrap scroll">
                <table className="sci-table compact">
                  <thead><tr><th>Time</th><th>Family</th><th>Location</th><th>Goes by</th><th>What</th></tr></thead>
                  <tbody>
                    {d.upcoming.map(u => (
                      <tr key={u.lead_id + u.at} style={{ cursor: 'pointer' }} onClick={() => openPerson && openPerson(u.lead_id)}>
                        <td className="sci-small"><b>{timeOf(u.at)}</b></td>
                        <td className="sci-cell-main">{u.name}</td>
                        <td className="sci-small">{u.location || '—'}</td>
                        <td>{u.channels}</td>
                        <td className="sci-small">{u.what}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          <Panel title="Sent" labelledBy="sci-ob-sent"
            aside={<FilterChips items={[['all', 'All'], ['text', 'Texts'], ['email', 'Emails']]} value={show} onChange={setShow} label="Show" />}>
            {sent.length === 0 ? <Empty icon="send" title="Nothing sent yet">Messages show here the moment they go out.</Empty> : (
              <div className="sci-tablewrap scroll">
                <table className="sci-table compact">
                  <thead><tr><th>Time</th><th>Family</th><th>By</th><th>Message</th><th>Status</th></tr></thead>
                  <tbody>
                    {sent.map((s, i) => {
                      const [label, tone] = STATUS[s.status] || [s.status, '']
                      return (
                        <tr key={i} style={{ cursor: 'pointer' }} onClick={() => openPerson && openPerson(s.lead_id)}>
                          <td className="sci-small"><b>{timeOf(s.at)}</b></td>
                          <td className="sci-cell-main">{s.name}<small>{s.location || ''}</small></td>
                          <td><span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><Icon name={s.channel === 'text' ? 'sms' : 'mail'} />{s.channel === 'text' ? 'Text' : 'Email'}</span><small className="sci-muted" style={{ display: 'block' }}>{s.to || ''}</small></td>
                          <td className="sci-small" style={{ maxWidth: 520 }}>{s.subject && <b style={{ display: 'block' }}>{s.subject}</b>}{s.preview}</td>
                          <td><Chip tone={tone} plain>{label}</Chip></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}
    </>
  )
}
