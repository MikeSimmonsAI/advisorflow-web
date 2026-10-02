/**
 * Mobile-native agency screens - the phone no longer drops an agent onto the
 * desktop prospect page.
 *
 *   /m/prospects/:leadId   GET /agency/prospects/{id}            (server-scoped)
 *                          POST /agency/assignments/{aid}/accept|decline
 *   BrainStrip             GET/POST /conversation-intel/leads/{id}[/mode|/compose]
 *
 * Every permission is the server's: an offered agent sees the prospect read-only
 * until they accept, exactly as on desktop; a guessed id is a 404.
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useApi, useMobile, ScreenHead, Loading, ErrorState, Empty, Icon } from './MobileShell'
import { humanize, relTime } from './mobileHelpers'

const ACTION = {
  reply: 'Reply', wait: 'Wait', call: 'Call', schedule: 'Schedule', request_info: 'Ask for info',
  nurture: 'Nurture', human_takeover: 'A person should handle this', do_nothing: 'Nothing to do',
  create_task: 'Create task', escalate: 'Escalate',
}

/** Next best action + who is driving + one-tap takeover, for a phone. */
export function BrainStrip({ leadId, onDraft, channel = 'sms', compact = false }) {
  const { observing } = useMobile()
  const [ctx, setCtx] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  // On the thread the strip starts folded to one line so the messages stay on
  // screen at 360px; a tap shows the open questions and follow-up.
  const [expanded, setExpanded] = useState(!compact)
  useEffect(() => {
    let live = true
    api.get('/conversation-intel/leads/' + encodeURIComponent(leadId))
      .then(c => { if (live) setCtx(c) }).catch(e => { if (live) setErr(e) })
    return () => { live = false }
  }, [leadId])
  if (err || !ctx) return null                 // never block the screen on the brain
  const st = ctx.state || {}
  const nba = ctx.next_best_action || {}
  async function mode(m) {
    setBusy(true)
    try { setCtx(await api.post(`/conversation-intel/leads/${leadId}/mode`, { mode: m })) } catch (e) { setErr(e) }
    finally { setBusy(false) }
  }
  async function suggest() {
    setBusy(true)
    try {
      const d = await api.post(`/conversation-intel/leads/${leadId}/compose`, { channel, style: 'shorter' })
      onDraft && onDraft(d.suggestion || '', d.quality)
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  return (
    <div className={'mbrain' + (st.needs_human_reason ? ' mbrain--human' : '')} data-testid="m-brain">
      <button type="button" className="mbrain-row mbrain-toggle" aria-expanded={expanded}
        onClick={() => setExpanded(v => !v)}>
        <span className="mbrain-action">{ACTION[nba.action] || humanize(nba.action || '')}</span>
        <span className="mpill mpill--task">{humanize(st.mode || '')}</span>
      </button>
      <div className={'mbrain-reason' + (expanded ? '' : ' mbrain-reason--clip')}>{nba.reason}</div>
      {expanded && (ctx.open_questions || []).slice(0, 2).map(q => <div key={q.id} className="mbrain-q">“{q.value}”</div>)}
      {expanded && ctx.follow_up && <div className="mbrain-q">Follow up: {ctx.follow_up.value}{ctx.follow_up.date ? ' · ' + ctx.follow_up.date : ''}</div>}
      {!observing && (
        <div className="mbrain-btns">
          {st.mode === 'human_active'
            ? <button type="button" className="mbtn" disabled={busy} onClick={() => mode('ai_active')}>Resume AI</button>
            : <button type="button" className="mbtn" disabled={busy} onClick={() => mode('human_active')}>Take over</button>}
          {onDraft && <button type="button" className="mbtn" disabled={busy} onClick={suggest}>Suggest reply</button>}
        </div>
      )}
    </div>
  )
}

export function MobileProspect() {
  const { leadId } = useParams()
  const { observing, refreshSummary } = useMobile()
  const { data: p, error, loading, reload } = useApi('/agency/prospects/' + encodeURIComponent(leadId))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  if (loading && !p) return <div className="mscreen"><ScreenHead title="Prospect" back="/m" /><Loading /></div>
  if (error) return <div className="mscreen"><ScreenHead title="Prospect" back="/m" /><ErrorState error={error} onRetry={reload} /></div>
  const offer = (p.assignment_history || []).find(a => a.state === 'offered')
  const readOnly = p.assignment_state === 'offered' && !p.assigned_agent
  async function act(verb) {
    if (!offer) return
    setBusy(true); setMsg(null)
    try {
      await api.post(`/agency/assignments/${offer.id}/${verb}`, {})
      setMsg(verb === 'accept' ? 'Accepted - this prospect is yours.' : 'Declined.')
      reload(); refreshSummary && refreshSummary()
    } catch (e) { setMsg(e?.message || 'That did not go through.') }
    finally { setBusy(false) }
  }
  const tel = p.phone ? 'tel:' + String(p.phone).replace(/[^\d+]/g, '') : null
  return (
    <div className="mscreen" data-testid="m-prospect">
      <ScreenHead title={p.name || 'Prospect'} sub={[humanize(p.intent_level ? p.intent_level + ' intent' : ''), humanize(p.status)].filter(Boolean).join(' · ')} back="/m" />
      {msg && <div className="mok" role="status">{msg}</div>}
      {offer && !observing && (
        <div className="moffer" data-testid="m-offer">
          <strong>Offered to {offer.agent?.name || 'an agent'}</strong>
          <span>{offer.expires_at ? 'Expires ' + relTime(offer.expires_at) : ''}</span>
          <div className="mbrain-btns">
            <button type="button" className="mbtn mbtn--primary" disabled={busy} onClick={() => act('accept')}>Accept</button>
            <button type="button" className="mbtn" disabled={busy} onClick={() => act('decline')}>Decline</button>
          </div>
        </div>
      )}
      {readOnly && <div className="mstate">Accept the offer to work this prospect.</div>}
      {!readOnly && <BrainStrip leadId={leadId} />}
      <div className="mactions">
        {!readOnly && <Link className="mbtn mbtn--primary" to={'/m/conversations/' + p.id}><Icon name="chat" size={18} /> Conversation</Link>}
        {tel && !readOnly && <a className="mbtn" href={tel}>Call</a>}
        <Link className="mbtn" to={'/m/contacts/' + p.id}>Contact</Link>
      </div>
      <div className="msection-title">Details</div>
      <ul className="mlist">
        <li className="mrow"><span className="mrow-main"><span className="mrow-title">Agent</span><span className="mrow-detail">{p.assigned_agent?.name || 'Not assigned'}</span></span></li>
        <li className="mrow"><span className="mrow-main"><span className="mrow-title">Contact</span><span className="mrow-detail">{[p.phone, p.email].filter(Boolean).join(' · ') || 'Not on file'}</span></span></li>
        {(p.applications || []).map(a => (
          <li key={a.id} className="mrow"><span className="mrow-main"><span className="mrow-title">Application · {humanize(a.product_category) || 'Product'}</span>
            <span className="mrow-detail">{humanize(a.status)}{a.stalled ? ' · stalled' : ''}</span></span></li>))}
        {(p.policies || []).map(x => (
          <li key={x.id} className="mrow"><span className="mrow-main"><span className="mrow-title">Policy {x.policy_number || ''}</span>
            <span className="mrow-detail">{humanize(x.status)}</span></span></li>))}
        {!(p.applications || []).length && !(p.policies || []).length && <li className="mrow"><Empty>No applications or policies yet.</Empty></li>}
      </ul>
      <p className="mstate mstate--small"><Link to={'/agency/prospects/' + p.id}>Open the full record</Link></p>
    </div>
  )
}
