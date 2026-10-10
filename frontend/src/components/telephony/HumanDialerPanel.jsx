// Human dialer for one lead (S10, 2026-10-01).
//
// What this panel shows, and where each piece comes from:
//   Caller ID / public number   GET /dialer/identity - the outreach number a
//                               call uses and the business's published number,
//                               kept separate; "Not configured" when missing.
//   Call action + live state    CallButton (provider bridge, or an honest
//                               tel: fallback when the provider isn't set up)
//   Notes + disposition + task  DispositionModal (POST /calls/{id}/disposition)
//   Call history + voicemail    GET /dialer/leads/{id}/history
//   Next call                   GET /dialer/queue - the user's callable leads,
//                               already filtered by DNC / suppression / consent.
// Nothing here invents a status: an empty history says so.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import CallButton from './CallButton'
import VoicemailPlayer from './VoicemailPlayer'
import { OUTCOMES } from './DispositionModal'
import './telephony.css'

const OUTCOME_LABEL = Object.fromEntries(OUTCOMES)

function fmt(v) {
  if (!v) return ''
  const d = String(v).replace(/\D/g, '')
  const t = d.length === 11 && d.startsWith('1') ? d.slice(1) : d
  return t.length === 10 ? `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}` : v
}

function when(iso) {
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function dur(s) {
  if (s == null) return null
  const m = Math.floor(s / 60)
  return m ? `${m}m ${s % 60}s` : `${s}s`
}

function callTitle(c) {
  const dir = c.direction === 'inbound' ? 'Inbound' : 'Outbound'
  const how = c.provider === 'manual' ? ' · from own phone' : c.is_human_call ? ' · bridge' : c.direction === 'inbound' ? '' : ' · AI'
  return `${dir}${how}`
}

// blockedReason: when set (an internal test record), no call control is
//   offered at all - the reason is shown in its place.
// compact: the Lead Command Center's Calls tab; drops the queue-exclusion note.
// onHistory: hands the loaded history to the page (voicemail count) so it is
//   fetched once, not twice.
export default function HumanDialerPanel({ leadId, phone, blockedReason = null, compact = false, onHistory }) {
  const nav = useNavigate()
  const [ident, setIdent] = useState(null)
  const [hist, setHist] = useState(null)
  const [queue, setQueue] = useState(null)
  const [err, setErr] = useState('')
  const onHistoryRef = useRef(onHistory)
  useEffect(() => { onHistoryRef.current = onHistory }, [onHistory])

  const loadHist = useCallback(() => {
    if (!leadId) return
    api.get(`/dialer/leads/${leadId}/history`).then(h => { setHist(h); onHistoryRef.current?.(h) }).catch(e => setErr(e.message || 'History unavailable.'))
  }, [leadId])

  const loadQueue = useCallback(() => {
    api.get('/dialer/queue?limit=6').then(setQueue).catch(() => setQueue(null))
  }, [])

  useEffect(() => {
    setHist(null); setErr('')
    api.get('/dialer/identity').then(setIdent).catch(() => setIdent(null))
    loadHist(); loadQueue()
  }, [leadId, loadHist, loadQueue])

  const vo = ident?.voice_outbound
  const pub = ident?.public_contact
  const next = (queue?.items || []).filter(i => i.lead_id !== leadId)

  return (
    <div className="tel-panel" data-testid="human-dialer">
      <dl className="tel-ident">
        <dt>Caller ID</dt>
        <dd className={vo?.configured ? '' : 'tel-missing'}>
          {ident == null ? '…' : vo?.configured ? fmt(vo.e164) : 'Not configured'}
          {vo?.configured && !vo.provider_ready && ' (provider account not connected)'}
        </dd>
        <dt>Public number</dt>
        <dd className={pub?.ok ? '' : 'tel-missing'}>{ident == null ? '…' : pub?.ok ? fmt(pub.e164) : 'Not configured'}</dd>
      </dl>

      {blockedReason
        ? <div className="tel-box tel-box--error" role="note" data-testid="dialer-blocked">{blockedReason}</div>
        : <CallButton leadId={leadId} phone={phone} onDone={() => { loadHist(); loadQueue() }} />}

      <div className="tel-subhead">
        <span>Call history</span>
        {hist?.unreviewed_voicemails > 0 && (
          <span className="tel-pill tel-pill--vm" title="Voicemails from this contact not yet reviewed">
            {hist.unreviewed_voicemails} new voicemail{hist.unreviewed_voicemails > 1 ? 's' : ''}
          </span>
        )}
      </div>
      {err && <div className="tel-box tel-box--error">{err}</div>}
      {hist && hist.calls.length === 0 && hist.voicemails.length === 0 && (
        <p className="tel-note">No calls with this contact yet.</p>
      )}
      {hist && (hist.calls.length > 0 || hist.voicemails.length > 0) && (
        <ul className="tel-list">
          {/* A voicemail that belongs to a call below is shown on that call, not twice. */}
          {hist.voicemails.filter(v => !v.call_id).map(v => (
            <li key={`vm-${v.id}`}>
              <span><span className="tel-pill tel-pill--vm">{v.status === 'new' ? 'New voicemail' : 'Voicemail'}</span> {when(v.received_at)}</span>
              <span className="tel-meta">{dur(v.duration_seconds) || 'Length unknown'} · from {fmt(v.from_phone) || 'withheld'}</span>
              {v.transcript && <span className="tel-meta">“{v.transcript}”</span>}
              {v.audio_url && <VoicemailPlayer path={v.audio_url} />}
            </li>
          ))}
          {hist.calls.slice(0, 8).map(c => (
            <li key={c.id}>
              <span>
                <strong>{callTitle(c)}</strong> · {when(c.created_at)}
                {c.voicemail_state === 'left' && <> <span className="tel-pill tel-pill--vm">VM left</span></>}
                {c.voicemail_state === 'machine' && <> <span className="tel-pill tel-pill--vm">Machine</span></>}
                {c.voicemail_id && <> <span className="tel-pill tel-pill--vm">{c.voicemail_status === 'new' ? 'New voicemail' : 'Voicemail'}</span></>}
              </span>
              <span className="tel-meta">
                {c.disposition ? (OUTCOME_LABEL[c.disposition] || c.disposition.replace(/_/g, ' '))
                  : c.outcome ? c.outcome.replace(/_/g, ' ') : c.status.replace(/_/g, ' ')}
                {dur(c.duration_seconds) ? ` · ${dur(c.duration_seconds)}` : ''}
                {c.from_phone ? ` · from ${fmt(c.from_phone)}` : ''}
              </span>
              {c.disposition_notes && <span className="tel-meta">“{c.disposition_notes}”</span>}
              {c.voicemail_transcript && <span className="tel-meta">Voicemail: “{c.voicemail_transcript}”</span>}
              {c.voicemail_audio_url && <VoicemailPlayer path={c.voicemail_audio_url} />}
              {c.recording_audio_url && <VoicemailPlayer path={c.recording_audio_url} label="Play call recording" />}
            </li>
          ))}
        </ul>
      )}

      <div className="tel-subhead"><span>Next call</span>
        {queue && <span className="tel-meta">{queue.total_callable} callable</span>}</div>
      {queue && next.length === 0 && <p className="tel-note">No other callable leads assigned to you.</p>}
      {next.length > 0 && (
        <ul className="tel-list">
          {next.slice(0, 5).map(i => (
            <li key={i.lead_id}>
              <button type="button" className="tel-linkrow" onClick={() => nav(`/leads/${i.lead_id}`)}>
                <strong>{i.name || 'Unnamed lead'}</strong>
                <span className="tel-meta">{fmt(i.phone)} · {i.reason}
                  {i.last_called_at ? ` · last ${when(i.last_called_at)}` : ''}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!compact && queue?.excluded?.length > 0 && (
        <p className="tel-note">
          Not in the queue: {queue.excluded.map(e => `${e.count} · ${e.reason.replace(/\.$/, '')}`).join('; ')}
        </p>
      )}
    </div>
  )
}
