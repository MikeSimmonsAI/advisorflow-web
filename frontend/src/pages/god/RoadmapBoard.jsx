/**
 * RoadmapBoard — authoritative platform feature-status board.
 *
 * GOD-04: the single consumer of frontend/src/data/platformRoadmap.json.
 * All status, progress, and categorisation comes from that file.
 * Nothing is hardcoded here except colour tokens and label maps.
 *
 * Replaces the old hardcoded NEXT_MODULES chip list in ProductStatus.
 */
import { useState, useMemo } from 'react'
import roadmapData from '../../data/platformRoadmap.json'

const BOARD_STYLES = `
.rm-board { font-family: var(--god-font, system-ui, sans-serif); }

/* ── tokens ── */
.rm-board {
  --rm-bg:       var(--god-card, #fff);
  --rm-bg2:      var(--god-surface, #f9fafb);
  --rm-border:   var(--god-border, #e5e7eb);
  --rm-ink:      var(--god-text, #1f2937);
  --rm-muted:    var(--god-muted, #6b7280);
  --rm-faint:    var(--god-dim, #9ca3af);
  --rm-row-sep:  var(--god-border, #f3f4f6);
  --rm-input-bg: var(--god-field, #fff);
  --rm-tab-sel:  var(--god-accent, #3b82f6);
}

[data-appearance="dark"] .rm-board {
    --rm-bg:       #131c2b;
    --rm-bg2:      #0d1524;
    --rm-border:   #1e2e44;
    --rm-ink:      #d4dfef;
    --rm-muted:    #7a9cbf;
    --rm-faint:    #4a6680;
    --rm-row-sep:  #172236;
    --rm-input-bg: #0c1726;
    --rm-tab-sel:  #3b82f6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-appearance="light"]) .rm-board {
    --rm-bg:       #131c2b;
    --rm-bg2:      #0d1524;
    --rm-border:   #1e2e44;
    --rm-ink:      #d4dfef;
    --rm-muted:    #7a9cbf;
    --rm-faint:    #4a6680;
    --rm-row-sep:  #172236;
    --rm-input-bg: #0c1726;
    --rm-tab-sel:  #3b82f6;
  }
}

/* ── layout pieces ── */
.rm-section { margin-bottom:18px; border:1px solid var(--rm-border); border-radius:10px; background:var(--rm-bg); overflow:hidden; }
.rm-section-hd { padding:12px 18px; background:var(--rm-bg2); border-bottom:1px solid var(--rm-border); }
.rm-section-title { font-size:12px; font-weight:700; color:var(--rm-ink); }
.rm-section-meta { font-size:11px; color:var(--rm-faint); }
.rm-items { padding:0 18px; }
.rm-row { border-bottom:1px solid var(--rm-row-sep); padding:9px 0; }
.rm-row:last-child { border-bottom:none; }
.rm-row-main { display:flex; gap:8px; align-items:flex-start; cursor:pointer; }
.rm-row-main.no-detail { cursor:default; }
.rm-id { font-size:10px; font-family:monospace; color:var(--rm-faint); min-width:70px; padding-top:1px; flex-shrink:0; }
.rm-title { flex:1; font-size:12px; color:var(--rm-ink); line-height:1.5; }
.rm-row-meta { display:flex; gap:5px; align-items:center; flex-shrink:0; }
.rm-chevron { font-size:10px; color:var(--rm-faint); }
.rm-detail { margin-top:8px; margin-left:78px; font-size:11px; line-height:1.6; color:var(--rm-muted); }
.rm-alert { border-radius:6px; padding:6px 10px; margin-bottom:6px; }
.rm-alert-warn { background:#fffbeb; border:1px solid #fcd34d; color:#92400e; }
.rm-alert-err  { background:#fef2f2; border:1px solid #fca5a5; color:#991b1b; }
[data-appearance="dark"] .rm-alert-warn { background:#1c1600; border-color:#6b4c00; color:#fbbf24; }
[data-appearance="dark"] .rm-alert-err  { background:#1a0505; border-color:#7f1d1d; color:#fca5a5; }

/* ── filter tabs ── */
.rm-tabs { display:flex; gap:4px; }
.rm-tab { font-size:11px; padding:4px 12px; border-radius:100px; cursor:pointer; border:1px solid var(--rm-border); background:var(--rm-bg); color:var(--rm-muted); font-weight:600; transition:background .15s,color .15s; }
.rm-tab.active { border-color:var(--rm-tab-sel); background:color-mix(in srgb,var(--rm-tab-sel) 12%,transparent); color:var(--rm-tab-sel); }
.rm-tab-count { margin-left:5px; font-weight:400; color:var(--rm-faint); }

/* ── search ── */
.rm-search { font-size:12px; padding:5px 10px; border-radius:6px; border:1px solid var(--rm-border); background:var(--rm-input-bg); color:var(--rm-ink); flex:1; min-width:160px; max-width:280px; }
.rm-search::placeholder { color:var(--rm-faint); }

/* ── summary strip ── */
.rm-summary { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
.rm-summary-card { flex:1; min-width:100px; border:1px solid var(--rm-border); border-radius:8px; background:var(--rm-bg); padding:10px 14px; }
.rm-summary-n { font-size:22px; font-weight:800; line-height:1; color:var(--rm-ink); }
.rm-summary-l { font-size:10px; font-weight:600; color:var(--rm-faint); margin-top:3px; letter-spacing:.04em; text-transform:uppercase; }

/* ── progress bar ── */
.rm-prog { display:flex; align-items:center; gap:8px; }
.rm-prog-track { flex:1; height:5px; background:var(--rm-border); border-radius:100px; overflow:hidden; }
.rm-prog-fill  { height:100%; background:#22c55e; border-radius:100px; transition:width .3s; }
.rm-prog-label { font-size:10px; color:var(--rm-faint); min-width:46px; text-align:right; }

/* ── legend ── */
.rm-legend { display:flex; gap:8px; flex-wrap:wrap; margin-left:auto; align-items:center; }
.rm-legend-dot { width:8px; height:8px; border-radius:2px; display:inline-block; flex-shrink:0; }
.rm-legend-text { font-size:10px; color:var(--rm-muted); }

/* ── header row ── */
.rm-header { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
.rm-meta-line { font-size:10px; color:var(--rm-faint); margin-bottom:16px; }
.rm-empty { padding:24px; text-align:center; color:var(--rm-faint); font-size:13px; }
`

// ── colour tokens per status ──────────────────────────────────────────────────
const STATUS_META = {
  COMPLETE:        { label: 'Complete',      bg: 'var(--gm-pill-teal-bg)', text: 'var(--gm-teal)', border: 'var(--gm-teal)', dbg: 'var(--gm-pill-teal-bg)', dtxt: 'var(--gm-teal)', dborder: 'var(--gm-teal)' },
  FINISH:          { label: 'Finish',        bg: 'var(--gm-pill-amber-bg)', text: 'var(--gm-amber)', border: 'var(--gm-amber)', dbg: 'var(--gm-pill-amber-bg)', dtxt: 'var(--gm-amber)', dborder: 'var(--gm-pill-amber-bd)' },
  CONSOLIDATE:     { label: 'Consolidate',   bg: 'var(--gm-pill-amber-bg)', text: 'var(--gm-red)', border: 'var(--gm-amber)', dbg: 'var(--gm-pill-amber-bg)', dtxt: 'var(--gm-amber)', dborder: 'var(--gm-red)' },
  REMOVE:          { label: 'Remove',        bg: 'var(--gm-pill-purple-bg)', text: 'var(--gm-purple)', border: 'var(--gm-pill-purple-bd)', dbg: 'var(--gm-pill-purple-bg)', dtxt: 'var(--gm-purple)', dborder: 'var(--gm-purple)' },
  NOT_BUILT:       { label: 'Not built',     bg: 'var(--gm-pill-blue-bg)', text: 'var(--gm-blue)', border: 'var(--gm-blue)', dbg: 'var(--gm-pill-blue-bg)', dtxt: 'var(--gm-blue)', dborder: 'var(--gm-blue)' },
  POLICY_REQUIRED: { label: 'Policy needed', bg: 'var(--gm-pill-amber-bg)', text: 'var(--gm-amber)', border: 'var(--gm-amber)', dbg: 'var(--gm-pill-amber-bg)', dtxt: 'var(--gm-amber)', dborder: 'var(--gm-pill-amber-bd)' },
  BLOCKED:         { label: 'Blocked',       bg: 'var(--gm-pill-red-bg)', text: 'var(--gm-red)', border: 'var(--gm-pill-red-bd)', dbg: 'var(--gm-pill-red-bg)', dtxt: 'var(--gm-red)', dborder: 'var(--gm-red)' },
}
const PRIORITY_COLOR = { P0: 'var(--gm-red)', P1: 'var(--gm-amber)', P2: 'var(--gm-blue)', P3: 'var(--gm-text)' }

const NEEDS_ACTION = new Set(['FINISH','CONSOLIDATE','REMOVE','NOT_BUILT','POLICY_REQUIRED','BLOCKED'])
const FILTER_TABS = [
  { key: 'action',   label: 'Needs action' },
  { key: 'all',      label: 'All items'    },
  { key: 'complete', label: 'Complete'     },
]

// Dark mode: check data-appearance on root (same pattern as rest of app)
function useDark() {
  return document.documentElement.getAttribute('data-appearance') === 'dark' ||
    (!document.documentElement.getAttribute('data-appearance') &&
     window.matchMedia('(prefers-color-scheme: dark)').matches)
}

function StatusBadge({ status }) {
  const dark = useDark()
  const m = STATUS_META[status] || { label: status, bg: 'var(--gm-panel)', text: 'var(--gm-blue)', border: 'var(--gm-card-line)', dbg: 'var(--gm-pill-blue-bg)', dtxt: 'var(--gm-text)', dborder: 'var(--gm-blue)' }
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 100,
      background: dark ? m.dbg : m.bg,
      color: dark ? m.dtxt : m.text,
      border: `1px solid ${dark ? m.dborder : m.border}`,
      whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '.04em',
    }}>
      {m.label}
    </span>
  )
}

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="rm-prog">
      <div className="rm-prog-track">
        <div className="rm-prog-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="rm-prog-label">{done}/{total} ({pct}%)</span>
    </div>
  )
}

function ItemRow({ item }) {
  const [open, setOpen] = useState(false)
  const hasDetail = item.decision_required || item.next_action || item.blocked_reason || item.notes
  return (
    <div className="rm-row">
      <div className={`rm-row-main${hasDetail ? '' : ' no-detail'}`}
           onClick={() => hasDetail && setOpen(o => !o)}>
        <span className="rm-id">{item.id}</span>
        <span className="rm-title">{item.title}</span>
        <div className="rm-row-meta">
          {item.decision_required && (
            <span title="Decision required" style={{ fontSize: 10, color: 'var(--gm-amber)', fontWeight: 700 }}>⚑</span>
          )}
          <span style={{ fontSize: 10, fontWeight: 700, color: PRIORITY_COLOR[item.priority] || 'var(--rm-faint)' }}>
            {item.priority}
          </span>
          <StatusBadge status={item.status} />
          {hasDetail && <span className="rm-chevron">{open ? '▲' : '▼'}</span>}
        </div>
      </div>
      {open && hasDetail && (
        <div className="rm-detail">
          {item.decision_required && (
            <div className="rm-alert rm-alert-warn">
              <strong>Decision required:</strong> {item.decision_required}
            </div>
          )}
          {item.blocked_reason && (
            <div className="rm-alert rm-alert-err">
              <strong>Blocked:</strong> {item.blocked_reason}
            </div>
          )}
          {item.next_action && <div style={{ marginBottom: 4 }}><strong>Next:</strong> {item.next_action}</div>}
          {item.notes && <div>{item.notes}</div>}
        </div>
      )}
    </div>
  )
}

function SystemSection({ system, items, filter }) {
  const visible = items.filter(item => {
    if (filter === 'complete') return item.status === 'COMPLETE'
    if (filter === 'action')   return NEEDS_ACTION.has(item.status)
    return true
  })
  if (visible.length === 0) return null
  const done  = items.filter(i => i.status === 'COMPLETE').length
  const total = items.length
  return (
    <div className="rm-section">
      <div className="rm-section-hd">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <span className="rm-section-title">{system}</span>
          <span className="rm-section-meta">{visible.length} shown · {total} total</span>
        </div>
        <ProgressBar done={done} total={total} />
      </div>
      <div className="rm-items">
        {visible.map(item => <ItemRow key={item.id} item={item} />)}
      </div>
    </div>
  )
}

export default function RoadmapBoard() {
  const [filter, setFilter] = useState('action')
  const [search, setSearch] = useState('')

  const data = useMemo(() => {
    try {
      if (!roadmapData || !Array.isArray(roadmapData.items)) return null
      return roadmapData
    } catch (_) { return null }
  }, [])

  const grouped = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    const systems = data.systems || []
    return systems.map(sys => {
      let items = data.items.filter(i => i.system === sys)
      if (q) items = items.filter(i =>
        i.title.toLowerCase().includes(q) ||
        i.id.toLowerCase().includes(q) ||
        (i.notes || '').toLowerCase().includes(q) ||
        (i.next_action || '').toLowerCase().includes(q)
      )
      return { system: sys, items }
    }).filter(g => g.items.length > 0)
  }, [data, search])

  const totals = useMemo(() => {
    if (!data) return {}
    const byStatus = {}
    for (const item of data.items) byStatus[item.status] = (byStatus[item.status] || 0) + 1
    return {
      all:      data.items.length,
      complete: byStatus.COMPLETE || 0,
      action:   data.items.filter(i => NEEDS_ACTION.has(i.status)).length,
      blocked:  byStatus.BLOCKED || 0,
      decision: data.items.filter(i => i.decision_required).length,
    }
  }, [data])

  if (!data) return (
    <div className="rm-alert rm-alert-err" style={{ borderRadius: 10, padding: '20px 24px', fontSize: 13 }}>
      <strong>Roadmap data unavailable.</strong> Could not parse platformRoadmap.json.
    </div>
  )

  const pct = totals.all ? Math.round((totals.complete / totals.all) * 100) : 0

  return (
    <div className="rm-board">
      <style>{BOARD_STYLES}</style>

      {/* Summary strip */}
      <div className="rm-summary">
        <div className="rm-summary-card">
          <div className="rm-summary-n" style={{ color: 'var(--gm-teal)' }}>{pct}%</div>
          <div className="rm-summary-l">Complete</div>
        </div>
        <div className="rm-summary-card">
          <div className="rm-summary-n">{totals.action}</div>
          <div className="rm-summary-l">Need action</div>
        </div>
        <div className="rm-summary-card">
          <div className="rm-summary-n" style={{ color: 'var(--gm-red)' }}>{totals.blocked}</div>
          <div className="rm-summary-l">Blocked</div>
        </div>
        <div className="rm-summary-card">
          <div className="rm-summary-n" style={{ color: 'var(--gm-amber)' }}>{totals.decision}</div>
          <div className="rm-summary-l">Decisions needed</div>
        </div>
        <div className="rm-summary-card">
          <div className="rm-summary-n">{totals.complete}/{totals.all}</div>
          <div className="rm-summary-l">Items done</div>
        </div>
      </div>

      {/* Header row */}
      <div className="rm-header">
        <div className="rm-tabs">
          {FILTER_TABS.map(tab => (
            <button key={tab.key} className={`rm-tab${filter === tab.key ? ' active' : ''}`}
                    onClick={() => setFilter(tab.key)}>
              {tab.label}
              <span className="rm-tab-count">{totals[tab.key] ?? ''}</span>
            </button>
          ))}
        </div>
        <input className="rm-search" value={search} onChange={e => setSearch(e.target.value)}
               placeholder="Search items…" />
        <div className="rm-legend">
          {Object.entries(STATUS_META).slice(0,4).map(([k, v]) => (
            <span key={k} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span className="rm-legend-dot" style={{ background: v.bg, border: `1px solid ${v.border}` }} />
              <span className="rm-legend-text">{v.label}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="rm-meta-line">
        platformRoadmap.json · v{data.schema_version} · {data.generated_on}
        {' · '}{data.items.length} items · {data.systems?.length ?? 0} systems
      </div>

      {grouped.length === 0 ? (
        <div className="rm-empty">No items match the current filter.</div>
      ) : (
        grouped.map(g => <SystemSection key={g.system} system={g.system} items={g.items} filter={filter} />)
      )}
    </div>
  )
}
