/* OPERATIONS — communications, calendar, booking, AI and data, as the server
   observed them. "Configured" means stored, never "tested against a provider". */
import { useNavigate } from 'react-router-dom'
import { Pill } from './ccShared'

const OLD_TONE = { CONFIGURED: 'good', PARTIAL: 'warn', NOT_CONFIGURED: 'bad', NONE: 'neutral' }

export default function OperationsTab({ cc, onEnter }) {
  const nav = useNavigate()
  const items = Object.fromEntries(cc.readiness.items.map(i => [i.key, i]))
  const ops = cc.operations || {}
  const rows = [
    ['SMS / Twilio', items.sms, ops.communications_sms],
    ['Email sender', items.email, ops.communications_email],
    ['Booking', items.booking, ops.booking],
    ['AI automation', items.ai_automation, ops.ai],
  ]
  const sms = items.sms && items.sms.detail

  return (
    <div className="occ">
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Operations</h3>
            <p className="occ-sub">
              Stored configuration only. Nothing on this page has called Twilio, the email
              provider or a calendar API, and nothing here sends a message.
            </p>
          </div>
          <div className="occ-head-actions">
            <button className="go-btn" onClick={() => nav('/god/diagnostics/twilio')}>Twilio diagnostics</button>
            <button className="go-btn" onClick={() => nav('/god/ai-deployment')}>AI deployment</button>
            <button className="go-btn" onClick={onEnter}>Enter organization settings</button>
          </div>
        </div>
        <ul className="occ-list">
          {rows.map(([label, it, old]) => (
            <li key={label}>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{label}</strong>
                <div className="occ-muted">{it ? it.reason : (old ? old.reason : 'not tracked')}</div>
              </span>
              {it ? <Pill status={it.status} label={it.status_label} />
                  : <Pill tone="neutral" label="Not tracked" />}
            </li>
          ))}
          {ops.calendar && (
            <li>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>Calendar connections</strong>
                <div className="occ-muted">{ops.calendar.reason}</div>
              </span>
              <Pill tone={OLD_TONE[ops.calendar.status]} label={ops.calendar.status.replace(/_/g, ' ')} />
            </li>
          )}
          {ops.data && (
            <li>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>Data</strong>
                <div className="occ-muted">{ops.data.reason}</div>
              </span>
              <Pill tone={OLD_TONE[ops.data.status]} label={ops.data.status.replace(/_/g, ' ')} />
            </li>
          )}
        </ul>
      </section>

      {sms && (
        <section className="occ-card">
          <h3>SMS registration detail</h3>
          <p className="occ-sub">As stored on the organization record.</p>
          <div className="occ-facts" style={{ borderTop: 0, marginTop: 0, paddingTop: 0 }}>
            <div><div className="occ-fact-k">Account SID</div><div className="occ-fact-v">{sms.account_sid_present ? 'Stored' : 'Missing'}</div></div>
            <div><div className="occ-fact-k">Auth token</div><div className="occ-fact-v">{sms.auth_token_present ? 'Stored' : 'Missing'}</div></div>
            <div><div className="occ-fact-k">Org number</div><div className="occ-fact-v">{sms.org_number_present ? 'Stored' : 'None'}</div></div>
            <div><div className="occ-fact-k">User numbers</div><div className="occ-fact-v">{sms.user_numbers ?? '—'}</div></div>
            <div><div className="occ-fact-k">Number type</div><div className="occ-fact-v">{sms.number_type || '—'}</div></div>
            <div><div className="occ-fact-k">A2P brand</div><div className="occ-fact-v">{sms.a2p_brand_status || 'Not registered'}</div></div>
            <div><div className="occ-fact-k">A2P campaign</div><div className="occ-fact-v">{sms.a2p_campaign_status || 'Not registered'}</div></div>
          </div>
        </section>
      )}
    </div>
  )
}
