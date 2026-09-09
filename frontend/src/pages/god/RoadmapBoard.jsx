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

// ── colour tokens per status ──────────────────────────────────────────────────
const STATUS_META = {
  COMPLETE:        { label: 'Complete',        bg: '#f0fdf4', text: '#166534', border: '#86efac' },
  FINISH:          { label: 'Finish',          bg: '#fffbeb', text: '#92400e', border: '#fcd34d' },
  CONSOLIDATE:     { label: 'Consolidate',     bg: '#fff7ed', text: '#9a3412', border: '#fdba74' },
  REMOVE:          { label: 'Remove',          bg: '#fdf4ff', text: '#6b21a8', border: '#d8b4fe' },
  NOT_BUILT:       { label: 'Not built',       bg: '#eff6ff', text: '#1e40af', border: '#93c5fd' },
  POLICY_REQUIRED: { label: 'Policy needed',   bg: '#fef9c3', text: '#713f12', border: '#fde68a' },
  BLOCKED:         { label: 'Blocked',         bg: '#fef2f2', text: '#991b1b', border: '#fca5a5' },
}
const PRIORITY_COLOR = { P0: '#dc2626', P1: '#f59e0b', P2: '#3b82f6', P3: '#9ca3af' }

const NEEDS_ACTION = new Set(['FINISH','CONSOLIDATE','REMOVE','NOT_BUILT','POLICY_REQUIRED','BLOCKED'])
const FILTER_TABS = [
  { key: 'action',   label: 'Needs action' },
  { key: 'all',      label: 'All items'    },
  { key: 'complete', label: 'Complete'     },
]

function StatusBadge({ status }) {
  const m = STATUS_META[status] || { label: status, bg: '#f3f4f6', text: '#374151', border: '#d1d5db' }
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 100,
      background: m.bg, color: m.text, border: `1px solid ${m.border}`,
      whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '.04em',
    }}>
      {m.label}
    </span>
  )
}

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 5, background: '#e5e7eb', borderRadius: 100, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: '#22c55e', borderRadius: 100, transition: 'width .3s' }} />
      </div>
      <span style={{ fontSize: 10, color: '#9ca3af', minWidth: 46, textAlign: 'right' }}>
        {done}/{total} ({pct}%)
      </span>
    </div>
  )
}

function ItemRow({ item }) {
  const [open, setOpen] = useState(false)
  const hasDetail = item.decision_required || item.next_action || item.blocked_reason || item.notes
  return (
    <div style={{ borderBottom: '1px solid #f3f4f6', padding: '9px 0' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: hasDetail ? 'pointer' : 'default' }}
           onClick={() => hasDetail && setOpen(o => !o)}>
        <span style={{ fontSize: 10, fontFamily: 'monospace', color: '#9ca3af', minWidth: 70, paddingTop: 1 }}>
          {item.id}
        </span>
        <span style={{ flex: 1, fontSize: 12, color: '#1f2937', lineHeight: 1.5 }}>
          {item.title}
        </span>
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', flexShrink: 0 }}>
          {item.decision_required && (
            <span title="Decision required" style={{ fontSize: 10, color: '#b45309', fontWeight: 700 }}>⚑</span>
          )}
          <span style={{ fontSize: 10, fontWeight: 700, color: PRIORITY_COLOR[item.priority] || '#9ca3af' }}>
            {item.priority}
          </span>
          <StatusBadge status={item.status} />
          {hasDetail && (
            <span style={{ fontSize: 10, color: '#9ca3af' }}>{open ? '▲' : '▼'}</span>
          )}
        </div>
      </div>
      {open && hasDetail && (
        <div style={{ marginTop: 8, marginLeft: 78, fontSize: 11, lineHeight: 1.6, color: '#4b5563' }}>
          {item.decision_required && (
            <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6,
                          padding: '6px 10px', marginBottom: 6, color: '#92400e' }}>
              <strong>Decision required:</strong> {item.decision_required}
            </div>
          )}
          {item.blocked_reason && (
            <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 6,
                          padding: '6px 10px', marginBottom: 6, color: '#991b1b' }}>
              <strong>Blocked:</strong> {item.blocked_reason}
            </div>
          )}
          {item.next_action && (
            <div style={{ marginBottom: 4 }}><strong>Next:</strong> {item.next_action}</div>
          )}
          {item.notes && (
            <div style={{ color: '#6b7280' }}>{item.notes}</div>
          )}
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
    <div style={{ marginBottom: 20, border: '1px solid #e5e7eb', borderRadius: 10,
                  background: '#fff', overflow: 'hidden' }}>
      <div style={{ padding: '12px 18px', background: '#f9fafb',
                    borderBottom: '1px solid #e5e7eb' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: '#1f2937' }}>{system}</span>
          <span style={{ fontSize: 11, color: '#9ca3af' }}>
            {visible.length} shown · {total} total
          </span>
        </div>
        <ProgressBar done={done} total={total} />
      </div>
      <div style={{ padding: '0 18px' }}>
        {visible.map(item => <ItemRow key={item.id} item={item} />)}
      </div>
    </div>
  )
}

export default function RoadmapBoard() {
  const [filter, setFilter] = useState('action')
  const [search, setSearch] = useState('')

  // Safe fallback if JSON fails to parse or is malformed
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
    return {
      all:      data.items.length,
      complete: data.items.filter(i => i.status === 'COMPLETE').length,
      action:   data.items.filter(i => NEEDS_ACTION.has(i.status)).length,
    }
  }, [data])

  if (!data) return (
    <div style={{ border: '1px solid #fca5a5', background: '#fef2f2', borderRadius: 10,
                  padding: '20px 24px', color: '#991b1b', fontSize: 13 }}>
      <strong>Roadmap data unavailable.</strong> Could not parse platformRoadmap.json.
    </div>
  )

  return (
    <div style={{ fontFamily: 'var(--god-font, system-ui, sans-serif)',
                  color: 'var(--god-text, #1f2937)' }}>

      {/* Header row */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                    flexWrap: 'wrap', marginBottom: 16 }}>

        {/* Filter tabs */}
        <div style={{ display: 'flex', gap: 4 }}>
          {FILTER_TABS.map(tab => (
            <button key={tab.key} onClick={() => setFilter(tab.key)} style={{
              fontSize: 11, padding: '4px 12px', borderRadius: 100, cursor: 'pointer',
              border: filter === tab.key ? '1px solid #3b82f6' : '1px solid #e5e7eb',
              background: filter === tab.key ? '#eff6ff' : '#fff',
              color: filter === tab.key ? '#1d4ed8' : '#6b7280', fontWeight: 600,
            }}>
              {tab.label}
              <span style={{ marginLeft: 5, fontWeight: 400, color: '#9ca3af' }}>
                {totals[tab.key] ?? ''}
              </span>
            </button>
          ))}
        </div>

        {/* Search */}
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search items…"
          style={{ fontSize: 12, padding: '5px 10px', borderRadius: 6,
                   border: '1px solid #e5e7eb', flex: 1, minWidth: 160, maxWidth: 280 }}
        />

        {/* Legend */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginLeft: 'auto' }}>
          {Object.entries(STATUS_META).slice(0,4).map(([k, v]) => (
            <span key={k} style={{ fontSize: 10, display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: v.bg,
                             border: `1px solid ${v.border}`, display: 'inline-block' }} />
              <span style={{ color: '#6b7280' }}>{v.label}</span>
            </span>
          ))}
        </div>
      </div>

      {/* Source attribution */}
      <div style={{ fontSize: 10, color: '#9ca3af', marginBottom: 16 }}>
        Source: platformRoadmap.json · schema_version {data.schema_version} · generated {data.generated_on}
        {' · '}{data.items.length} items across {data.systems?.length ?? 0} systems
      </div>

      {/* Grouped systems */}
      {grouped.length === 0 ? (
        <div style={{ padding: '24px', textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>
          No items match the current filter.
        </div>
      ) : (
        grouped.map(g => (
          <SystemSection key={g.system} system={g.system} items={g.items} filter={filter} />
        ))
      )}
    </div>
  )
}
