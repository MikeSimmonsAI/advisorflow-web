/**
 * LAUNCH BOARD › Projects — what each project has actually built, merged and
 * shipped, read from GitHub (GET /god/launch-board/github, read-only).
 *
 * Every step is shown separately: committed, pushed, PR opened, merged, on
 * staging, in production, verified in production. A step the server cannot
 * check says "Unable to verify" — it is never filled in from a guess, and
 * there are no completion percentages.
 *
 * The relay, the owner-approved project queue and the history are unchanged
 * under the "Relay & project board" tab (/god/control-room).
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Page, Card, Kpi, Pill, Loading, ErrorState, Empty, ActionLink, Expandable, Steps, ago } from './ui/GodKit'

const STATUS = {
  released:    { tone: 'good', label: 'Released' },
  deploying:   { tone: 'warn', label: 'Merged, deploying' },
  merged:      { tone: 'info', label: 'Merged' },
  building:    { tone: 'info', label: 'Being built' },
  blocked:     { tone: 'bad',  label: 'Blocked' },
  needs_owner: { tone: 'warn', label: 'Needs your decision' },
  quiet:       { tone: 'off',  label: 'No recent activity' },
  unknown:     { tone: 'off',  label: 'Unable to verify' },
}
const CHECKS = {
  passing: ['good', 'Checks passing'], failing: ['bad', 'Checks failing'],
  pending: ['warn', 'Checks running'], none: ['off', 'No checks reported'],
  unknown: ['off', 'Checks: unable to verify'],
}
const PROD = { yes: ['good', 'In production'], no: ['warn', 'Not in production yet'], unknown: ['off', 'Production: unable to verify'] }

function PrLine ({ pr, showProd }) {
  return (
    <li className="gk-row">
      <div className="gk-row-main">
        <div className="gk-row-title">
          <a className="gk-link" href={pr.url} target="_blank" rel="noopener noreferrer">#{pr.number}</a>{' '}
          {pr.title}
        </div>
        <div className="gk-row-sub">
          <code>{pr.branch}</code>
          {pr.merged ? ` · merged ${ago(pr.merged_at)}` : ` · updated ${ago(pr.updated_at)}`}
          {pr.merge_sha ? <> · <code>{pr.merge_sha.slice(0, 7)}</code></> : null}
        </div>
      </div>
      {pr.draft ? <Pill tone="test">Draft</Pill> : null}
      {showProd && pr.in_production ? <Pill tone={PROD[pr.in_production][0]}>{PROD[pr.in_production][1]}</Pill> : null}
    </li>
  )
}

function ProjectCard ({ p }) {
  const st = STATUS[p.status] || STATUS.unknown
  const ck = CHECKS[p.checks?.state] || CHECKS.unknown
  const f = p.focus
  return (
    <Expandable id={'proj-' + p.key} header={
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span className="gk-card-title" style={{ margin: 0 }}>{p.label}</span>
          <Pill tone={st.tone}>{st.label}</Pill>
          {f ? <Pill tone={ck[0]}>{ck[1]}</Pill> : null}
          {p.last_activity ? <span className="gk-row-meta">{ago(p.last_activity)}</span> : null}
        </div>
        <Steps steps={p.ladder} />
        {p.blocker ? <div className="gk-row-sub" style={{ color: 'var(--gm-red)' }}>Blocker: {p.blocker}</div> : null}
        {p.next_action ? <div className="gk-row-sub">Next: {p.next_action}</div> : null}
      </div>
    }>
      {f ? (
        <div className="gk-row-sub" style={{ marginBottom: 10 }}>
          Latest: <a className="gk-link" href={f.url} target="_blank" rel="noopener noreferrer">PR #{f.number}</a>{' '}
          on <code>{f.branch}</code> · head <code>{(f.head_sha || '').slice(0, 7) || '—'}</code>
          {p.checks?.total ? ` · ${p.checks.passed} passed, ${p.checks.failed} failed, ${p.checks.pending} running` : ''}
        </div>
      ) : null}
      <div className="gk-two">
        <div>
          <div className="gk-eyebrow">Open pull requests</div>
          {p.open_prs.length ? <ul className="gk-list">{p.open_prs.map(pr => <PrLine key={pr.number} pr={pr} />)}</ul>
            : <Empty>None open.</Empty>}
        </div>
        <div>
          <div className="gk-eyebrow">Merged recently</div>
          {p.recently_merged.length ? <ul className="gk-list">{p.recently_merged.map(pr => <PrLine key={pr.number} pr={pr} showProd />)}</ul>
            : <Empty>Nothing merged in this window.</Empty>}
        </div>
      </div>
      <p className="gk-row-sub" style={{ marginTop: 10 }}>
        "On staging" and "Production verified" are not connected to an automatic source yet, so they always show as
        unable to verify. Record a live check in the Relay &amp; project board tab.
      </p>
    </Expandable>
  )
}

export default function LaunchBoardPage () {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback((refresh = false) => {
    setBusy(true); setErr(null)
    api.get('/god/launch-board/github' + (refresh ? '?refresh=true' : ''), { noOrgContext: true })
      .then(setData)
      .catch(e => setErr(e?.message || 'Could not load'))
      .finally(() => setBusy(false))
  }, [])
  useEffect(() => { load() }, [load])

  const projects = data?.projects || []
  const s = data?.summary || {}
  const working = projects.filter(p => p.status === 'released').length

  return (
    <Page eyebrow="Projects" title="Launch Board"
      sub="What is being built, what is merged, what is live and what is stuck — read from GitHub, not typed in."
      actions={<ActionLink onClick={() => load(true)} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh from GitHub'}</ActionLink>}>
      {err ? <ErrorState onRetry={() => load()}>The Launch Board could not be loaded: {err}</ErrorState> : null}
      {!data && !err ? <Loading>Reading GitHub…</Loading> : null}
      {data && !data.available ? (
        <Card title="Unable to verify">
          <p className="gk-row-sub">{data.reason}</p>
        </Card>
      ) : null}
      {data && data.available ? (
        <div className="gk-kpis" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 210px), 1fr))' }}>
          <Kpi icon="check" color="green" label="Working (released)" value={working} sub="projects whose latest merge is live" />
          <Kpi icon="rocket" color="blue" label="Being built" value={s.being_built} sub="open pull requests" />
          <Kpi icon="stop" color="red" label="Blocked" value={s.blocked} tone={s.blocked ? 'red' : undefined} sub="failing checks" />
          <Kpi icon="alert" color="orange" label="Needs your decision" value={s.needs_owner} tone={s.needs_owner ? 'amber' : undefined} sub="redesign drafts awaiting approval" />
          <Kpi icon="flag" color="purple" label="Released recently" value={s.released_recently} sub={`merged and live, last ${data.window_days} days`} />
        </div>
      ) : null}
      {data ? (
        <>
          <div className="gk-row-sub" style={{ margin: '4px 0 12px' }}>
            Production is running{' '}
            {data.deployed_commit ? <code>{data.deployed_commit.slice(0, 7)}</code> : <b>an unknown commit (unable to verify)</b>}
            {data.generated_at ? ` · checked ${ago(data.generated_at)}` : ''}
          </div>
          <div style={{ display: 'grid', gap: 12 }}>
            {projects.map(p => <ProjectCard key={p.key} p={p} />)}
          </div>
        </>
      ) : null}
    </Page>
  )
}
