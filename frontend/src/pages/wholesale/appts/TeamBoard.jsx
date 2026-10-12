/* Wholesale > Team Board (build list #19). Appointments set and run, and
 * contracts signed, by rep and by week. Counts only - straight from the
 * appointments. Server: GET /wholesale/appointments/board. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, EvoApp, Metric, Metrics, PageHead, Seg, Skeleton } from '../ds/ds'
import '../ds/evo-pages.css'
import { dayLabel } from './apptTime'
import './appts.css'

const pct = (n) => (n === null || n === undefined ? '-' : `${n}%`)

export default function TeamBoard() {
  const [weeks, setWeeks] = useState('4')
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setData(null)
    api.get(`/wholesale/appointments/board?weeks=${weeks}`).then(setData).catch((e) => setErr(e.message || 'Could not load the board.'))
  }, [weeks])
  const t = data?.team
  return (
    <EvoApp world="operations">
      <PageHead world="operations" eyebrow="Wholesale" title="Team Board"
                sub="Appointments set and run, and contracts signed - by rep and by week (weeks start Monday)." />
      <div className="ap-bar">
        <Seg label="How many weeks" value={weeks} onChange={setWeeks}
             items={[{ key: '1', label: 'This week' }, { key: '4', label: '4 weeks' }, { key: '12', label: '12 weeks' }]} />
      </div>
      <Alert>{err}</Alert>
      {!data && !err ? <Skeleton rows={4} height={40} /> : null}
      {t ? (
        <Metrics label="Team totals">
          <Metric label="Booked" value={t.booked} />
          <Metric label="Ran" value={t.ran} sub={t.no_show ? `${t.no_show} no-show` : null} />
          <Metric label="Signed" value={t.signed} sub={`${pct(t.signed_rate)} of visits run`} />
          <Metric label="Follow up" value={t.follow_up} />
          <Metric label="Offers waiting on you" value={t.waiting} attention={t.waiting > 0} />
        </Metrics>
      ) : null}
      {data ? (
        <div className="tb-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th scope="col">Rep</th>
                {data.weeks.map((w) => <th key={w} scope="col">Week of {dayLabel(w)}</th>)}
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.user_id || 'none'}>
                  <th scope="row"><div className="tb-name">{r.name}</div>{r.role_label ? <div className="evo-muted evo-small">{r.role_label}</div> : null}</th>
                  {r.weeks.map((w) => (
                    <td key={w.week} data-label={`Week of ${dayLabel(w.week)}`}>
                      <span className="tb-cell"><b>{w.booked}</b> set · <b>{w.ran}</b> ran · <b className={w.signed ? 'tb-win' : ''}>{w.signed}</b> signed</span>
                    </td>
                  ))}
                  <td data-label="Total">
                    <span className="tb-cell"><b>{r.total.booked}</b> set · <b>{r.total.ran}</b> ran · <b className={r.total.signed ? 'tb-win' : ''}>{r.total.signed}</b> signed</span>
                    <span className="evo-muted evo-small"> · {pct(r.total.signed_rate)} close · {r.total.no_show} no-show</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data.rows.length ? <p className="evo-muted">Nobody on the team yet. Add people on Wholesale &gt; Team.</p> : null}
        </div>
      ) : null}
    </EvoApp>
  )
}
