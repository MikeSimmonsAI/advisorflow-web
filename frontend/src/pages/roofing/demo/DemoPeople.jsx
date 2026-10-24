/**
 * Public demo: Conversations (synthetic threads) and one homeowner record.
 * Every message here is a fixture - nothing was ever sent, and every action
 * button shows what it WOULD do in a live company.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Avatar, Empty, Icon, Pill } from '../ui'
import { DEMO_BASE, HOMEOWNERS, STAGE_LABEL, THREADS, agoText, byId, fmtDate, fmtTime, fmtWhen, inspectionsFor } from './fixtures'
import { BOOK_DEMO, usePreview } from './demoCtx'

const BUCKET_LABEL = { needs_human: 'Needs you', ready_to_book: 'Ready to book', new_reply: 'New reply' }
const TAG = { interested: 'interested', question: 'question', booking: 'picked a time', not_now: 'not now' }

function Bubble({ e }) {
  if (e.sys) return <div className="rf-conv__sys"><Icon name="check" size={14} />{e.body} <span>{fmtTime(e.at)}</span></div>
  const inbound = e.dir === 'in'
  const cls = inbound ? 'in' : e.actor === 'ai' ? 'ai' : 'team'
  return (
    <div className={`rf-msg rf-msg--${cls}`}>
      {!inbound && (e.actor === 'ai' ? <Avatar ai size={30} /> : <Avatar name={e.sender} size={30} />)}
      <div className="rf-msg__col">
        <div className="rf-msg__bubble">{e.body}</div>
        <span className="rf-msg__meta">{inbound ? `${TAG[e.tag] || 'reply'} · ` : `${e.actor === 'ai' ? 'AI' : e.sender} · `}{fmtTime(e.at)}</span>
      </div>
      {inbound && <Avatar name="Homeowner" size={30} />}
    </div>
  )
}

export function DemoConversations() {
  const [params, setParams] = useSearchParams()
  const preview = usePreview()
  const leadId = params.get('lead') || ''
  const [q, setQ] = useState('')
  const [draft, setDraft] = useState('')
  const items = HOMEOWNERS.filter((h) => (THREADS[h.id] || []).length)
    .filter((h) => !q || `${h.name} ${h.address} ${h.roof_issue}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (b.bucket ? 1 : 0) - (a.bucket ? 1 : 0) || b.last - a.last)
  const h = byId[leadId]
  const thread = h ? THREADS[h.id] || [] : []
  const lastIn = [...thread].reverse().find((e) => e.dir === 'in')
  const set = (id) => { const p = new URLSearchParams(params); if (id) p.set('lead', id); else p.delete('lead'); setParams(p); setDraft('') }

  return (
    <div className="rf-page rf-conv-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Conversations</h1>
          <p className="rf-lede">Newest replies and anyone who needs a person come first. Read what the AI said, answer, or take over.</p>
        </div>
        <span className="rf-sample">Sample conversations</span>
      </div>
      <div className={`rf-conv ${leadId ? 'has-lead' : ''}`}>
        <section className="rf-panel rf-conv__list" aria-label="Inbox">
          <div className="rf-conv__listhead">
            <div className="rf-search">
              <Icon name="search" size={18} />
              <input className="rf-input" placeholder="Search name, address" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search conversations" />
            </div>
          </div>
          <div className="rf-conv__items">
            {items.map((it) => {
              const last = (THREADS[it.id] || []).slice(-1)[0]
              return (
                <button key={it.id} className={`rf-conv__item ${it.id === leadId ? 'active' : ''} ${it.unread ? 'unread' : ''}`} onClick={() => set(it.id)}>
                  <Avatar name={it.name} size={38} />
                  <span className="rf-conv__itemmain">
                    <span className="rf-conv__itemtop"><b>{it.name}</b><span className="rf-muted">{agoText(last.at)}</span></span>
                    <span className="rf-conv__snippet">{last.body}</span>
                    <span className="rf-conv__itemmeta">
                      {it.bucket ? <Pill tone={it.bucket}>{BUCKET_LABEL[it.bucket]}</Pill> : <Pill tone="slate">{STAGE_LABEL[it.stage]}</Pill>}
                      <span className="rf-muted rf-conv__issue">{it.roof_issue}</span>
                      {it.unread > 0 && <span className="rf-conv__badge">{it.unread}</span>}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        </section>

        {h ? (
          <>
            <section className="rf-panel rf-conv__thread" aria-label="Conversation">
              <header className="rf-conv__threadhead">
                <button className="rf-btn rf-btn--ghost rf-btn--sm rf-conv__back" onClick={() => set('')} aria-label="Back to inbox"><Icon name="back" size={16} /></button>
                <Avatar name={h.name} size={44} />
                <div className="rf-conv__who">
                  <b>{h.name}</b>
                  <span className="rf-muted"><Icon name="phone" size={14} /> {h.phone} <Icon name="pin" size={14} /> {h.address}</span>
                </div>
                <div className="rf-conv__threadactions">
                  <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => preview('Take over', `The AI and the follow-up pause for ${h.first}, and your team answers from here. "Hand back to AI" resumes it.`)}><Icon name="hand" size={16} />Take over</button>
                  <Link className="rf-btn rf-btn--primary rf-btn--sm" to={`${DEMO_BASE}/homeowners/${h.id}`}><Icon name="users" size={16} />Homeowner</Link>
                </div>
              </header>
              {h.bucket === 'needs_human' && <div className="rf-conv__modebar"><Icon name="hand" size={16} />The AI paused and handed {h.first} to a person: {h.reason.toLowerCase()}.</div>}
              <div className="rf-conv__msgs">
                {thread.map((e, i) => <Bubble key={i} e={e} />)}
              </div>
              <div className="rf-conv__composer">
                <div className="rf-conv__compose">
                  <textarea className="rf-textarea" rows={3} placeholder="Type a message… (demo - nothing is sent)" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Message" />
                  <div className="rf-conv__composebar">
                    <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => setDraft(lastIn?.tag === 'question'
                      ? `Hi ${h.first}, great question - yes, our inspector can meet your adjuster. Would Thursday at 10:00 AM work?`
                      : `Hi ${h.first}, happy to help. I have Tuesday at 10:00 AM or Wednesday at 1:00 PM - which works?`)}><Icon name="spark" size={16} />AI draft</button>
                    <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={() => preview('Offer inspection times', `In a live workspace this reads ${h.owner}'s real calendar and drops the next open times into the message.`)}><Icon name="calendar" size={16} />Offer inspection times</button>
                    <span className="rf-conv__count rf-muted">{draft.length}/1600</span>
                    <button className="rf-btn rf-btn--primary rf-btn--sm" disabled={!draft.trim()} onClick={() => preview('Send', `In a live workspace this text goes to ${h.first} through the platform's send checks (consent, opt-out, quiet hours). In this demo nothing is sent.`)}><Icon name="send" size={16} />Send</button>
                  </div>
                </div>
              </div>
            </section>
            <aside className="rf-panel rf-conv__details" aria-label="Homeowner details">
              <div className="rf-conv__next"><span className="rf-muted">Next action</span><b>{h.next}</b><Pill tone="blue">{STAGE_LABEL[h.stage]}</Pill></div>
              <div className="rf-conv__sect">
                <div className="rf-conv__secthead"><h3>Homeowner</h3><Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/homeowners/${h.id}`}>Open record</Link></div>
                <dl className="rf-dl">
                  <dt>Phone</dt><dd>{h.phone}</dd><dt>Address</dt><dd>{h.address}</dd><dt>Source</dt><dd>{h.source}</dd><dt>Owner</dt><dd>{h.owner}</dd>
                </dl>
              </div>
              <div className="rf-conv__sect">
                <div className="rf-conv__secthead"><h3>Roof</h3></div>
                <dl className="rf-dl"><dt>Roof issue</dt><dd>{h.roof_issue}</dd><dt>Repair or replace</dt><dd>{h.service.replace('_', ' ')}</dd><dt>Urgency</dt><dd>{h.urgency}</dd><dt>Roof age</dt><dd>{h.roof_age} years</dd></dl>
              </div>
              {inspectionsFor(h.id).map((i) => (
                <div className="rf-conv__sect" key={i.id}>
                  <div className="rf-conv__secthead"><h3>Inspection</h3></div>
                  <p className="rf-conv__insp"><Icon name="calendar" size={16} /><b>{fmtWhen(i.scheduled)}</b></p>
                  <p className="rf-muted">{i.type} with {i.inspector} · {i.status === 'completed' ? `report ${i.report}` : i.status === 'in_progress' ? 'in progress' : 'booked'}</p>
                  <Link className="rf-btn rf-btn--ghost rf-btn--sm" style={{ marginTop: 8 }} to={i.status === 'completed' ? `${DEMO_BASE}/inspections/${i.id}/report` : `${DEMO_BASE}/inspections/${i.id}`}>{i.status === 'completed' ? 'View report' : 'Open inspection'}</Link>
                </div>
              ))}
            </aside>
          </>
        ) : (
          <section className="rf-panel rf-conv__placeholder">
            <Empty icon="chat" title="Pick a conversation">Choose a homeowner on the left to see what the AI said, what they said, and what comes next. Try Linda Nguyen for a full journey to a booked inspection.</Empty>
          </section>
        )}
      </div>
    </div>
  )
}

export function DemoHomeowner() {
  const { id } = useParams()
  const nav = useNavigate()
  const preview = usePreview()
  const h = byId[id]
  if (!h) return <main className="rf-page"><section className="rf-panel" style={{ padding: 24 }}><h1 className="rf-h1">Homeowner not found</h1><Link className="rf-btn rf-btn--primary" to={`${DEMO_BASE}/leads`}>Back to Leads &amp; Data</Link></section></main>
  const list = inspectionsFor(h.id)
  const open = list.find((i) => i.status === 'in_progress')
  const upcoming = list.find((i) => i.status === 'upcoming')
  const done = list.filter((i) => i.status === 'completed')
  const report = done[0]
  return (
    <div className="rf-page rf-ho">
      <div className="ri-top">
        <Link to={`${DEMO_BASE}/leads`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="back" size={16} />Leads &amp; data</Link>
        {(THREADS[h.id] || []).length > 0 && <Link to={`${DEMO_BASE}/conversations?lead=${h.id}`} className="rf-btn rf-btn--ghost rf-btn--sm"><Icon name="chat" size={16} />Conversation</Link>}
      </div>
      <section className="rf-panel rf-ho__head">
        <Avatar name={h.name} size={54} />
        <div className="rf-ho__who">
          <h1 className="rf-h1">{h.name}</h1>
          <p className="rf-muted"><Icon name="pin" size={15} /> {h.address}</p>
          <div className="rf-ho__pills">
            <Pill tone={h.stage === 'won' ? 'green' : h.stage === 'lost' ? 'slate' : 'blue'}>{STAGE_LABEL[h.stage]}</Pill>
            <Pill tone={h.urgency === 'high' ? 'orange' : 'slate'}>{h.urgency}</Pill>
            <Pill tone="amber">Sample data</Pill>
          </div>
        </div>
      </section>
      <section className="rf-panel" aria-label="Inspection">
        <h2 className="rf-panel__title">Inspection</h2>
        {upcoming && <p className="rf-ho__booked"><Icon name="calendar" size={16} />Booked: <b>{fmtWhen(upcoming.scheduled)}</b> · {upcoming.type} with {upcoming.inspector}</p>}
        <div className="rf-ho__btns">
          {open ? <Link className="rf-btn rf-btn--primary rf-add__big" to={`${DEMO_BASE}/inspections/${open.id}`}><Icon name="clipboard" size={18} />Continue inspection</Link>
            : upcoming ? <Link className="rf-btn rf-btn--primary rf-add__big" to={`${DEMO_BASE}/inspections/${upcoming.id}`}><Icon name="clipboard" size={18} />Open inspection</Link>
            : <button className={`rf-btn ${done.length ? 'rf-btn--ghost' : 'rf-btn--primary'} rf-add__big`} onClick={() => preview('Start inspection now', `In a live workspace this opens a new inspection for ${h.first} on your phone, records the real visit time and creates no calendar booking. To see a filled-in inspection, open James Carter's.`)}><Icon name="clipboard" size={18} />{done.length ? 'Start another inspection' : 'Start inspection now'}</button>}
          <button className="rf-btn rf-btn--ghost rf-add__big" onClick={() => preview('Book inspection', `In a live workspace this shows ${h.owner}'s real open times; booking puts it on the calendar. Nothing is booked in this demo.`)}><Icon name="calendar" size={18} />{upcoming ? 'Book another time' : 'Book inspection'}</button>
        </div>
        {done.length > 0 && (
          <ul className="rf-ho__reports">
            {done.map((i) => (
              <li key={i.id}>
                <div><b>Report {i.report}</b><span className="rf-muted">Completed {fmtDate(i.completed)} · {i.recommendation}</span></div>
                <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${DEMO_BASE}/inspections/${i.id}/report`}><Icon name="file" size={15} />View report</Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="rf-panel" aria-label="Sales">
        <h2 className="rf-panel__title">Sales</h2>
        <p><b>{h.stage === 'won' ? 'Job won' : h.stage === 'lost' ? `Closed lost: ${h.lost_reason}` : h.next}</b></p>
        <p className="rf-muted">{report?.estimate ? `Estimate $${report.estimate.toLocaleString()}` : 'No estimate yet'}{report?.won ? ` · job $${report.won.toLocaleString()}` : ''}</p>
        {report && h.stage !== 'won' && h.stage !== 'lost' && <button className="rf-btn rf-btn--primary rf-add__big" style={{ marginTop: 10 }} onClick={() => nav(`${DEMO_BASE}/inspections/${report.id}/report`)}><Icon name="dollar" size={18} />Next sales step</button>}
      </section>
      <div className="rf-grid-2">
        <section className="rf-panel">
          <h2 className="rf-panel__title">Homeowner</h2>
          <dl className="rf-dl"><dt>Phone</dt><dd>{h.phone}</dd><dt>Email</dt><dd>{h.email}</dd><dt>Address</dt><dd>{h.address}</dd><dt>Source</dt><dd>{h.source}</dd><dt>Salesperson</dt><dd>{h.owner}</dd></dl>
          <h3 className="rf-ho__h3">Can we contact them?</h3>
          <p className="rf-ho__qual"><span>Text</span><Pill tone="green">Ready to contact</Pill><span className="rf-muted">Consent on file, valid mobile number</span></p>
          <p className="rf-ho__qual"><span>Email</span><Pill tone="green">Ready to contact</Pill></p>
        </section>
        <section className="rf-panel">
          <h2 className="rf-panel__title">Roof</h2>
          <dl className="rf-dl"><dt>Problem</dt><dd>{h.roof_issue}</dd><dt>Repair or replace</dt><dd>{h.service.replace('_', ' ')}</dd><dt>Urgency</dt><dd>{h.urgency}</dd><dt>Roof age</dt><dd>{h.roof_age} years</dd></dl>
          <h3 className="rf-ho__h3">Activity</h3>
          {(THREADS[h.id] || []).slice(-3).reverse().map((e, i) => (
            <div key={i} className="rf-conv__note"><p>{e.body}</p><span className="rf-muted">{e.sys ? 'System' : e.dir === 'in' ? h.first : e.actor === 'ai' ? 'AI' : e.sender} · {agoText(e.at)}</span></div>
          ))}
        </section>
      </div>
      <section className="rf-panel dm-cta"><div><b>This is a sample homeowner.</b><span>See it with your own leads - we’ll set it up with your company.</span></div><a className="rf-btn rf-btn--primary" href={BOOK_DEMO}>Book a live demo</a></section>
    </div>
  )
}
