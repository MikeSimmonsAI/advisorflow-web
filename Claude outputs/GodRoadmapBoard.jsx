/**
 * GodRoadmapBoard — GOD-04: platform capability roadmap viewer.
 *
 * Reads frontend/src/data/platformRoadmap.json (imported statically — no API
 * call, no build change required). Groups 93 items across 15 systems by their
 * `status` and renders them in a filterable board.
 *
 * JSON shape per item:
 *   id, system, title, status, priority, depends_on[], completed_on,
 *   evidence, notes, decision_required, blocked_reason, next_action,
 *   previously_missed
 *
 * Status vocabulary:
 *   COMPLETE · FINISH · CONSOLIDATE · BLOCKED · POLICY_REQUIRED · NOT_BUILT · REMOVE
 */
import { useState, useMemo } from 'react'
import roadmapRaw from '../../data/platformRoadmap.json'

// ── normalise — handle both array-root and object-with-items shapes ──────────
const ALL_ITEMS = Array.isArray(roadmapRaw)
  ? roadmapRaw
  : (roadmapRaw.items || [])

const STATUS_META = {
  COMPLETE:        { label: 'Complete',       bg: '#f0fdf4', text: '#166534', border: '#86efac', dot: '#22c55e' },
  FINISH:          { label: 'Needs finishing', bg: '#fffbeb', text: '#92400e', border: '#fcd34d', dot: '#f59e0b' },
  CONSOLIDATE:     { label: 'Consolidate',    bg: '#faf5ff', text: '#6b21a8', border: '#d8b4fe', dot: '#a855f7' },
  BLOCKED:         { label: 'Blocked',        bg: '#eff6ff', text: '#1e40af', border: '#93c5fd', dot: '#3b82f6' },
  POLICY_REQUIRED: { label: 'Policy needed',  bg: '#fff7ed', text: '#9a3412', border: '#fdba74', dot: '#f97316' },
  NOT_BUILT:       { label: 'Not built',      bg: '#f9fafb', text: '#374151', border: '#e5e7eb', dot: '#6b7280' },
  REMOVE:          { label: 'Remove',         bg: '#fef2f2', text: '#991b1b', border: '#fca5a5', dot: '#ef4444' },
}

const PRIORITY_ORDER = { P0: 0, P1: 1, P2: 2, P3: 3 }

function statusMeta(s) {
  return STATUS_META[s] || { label: s, bg: '#f9fafb', text: '#374151', border: '#e5e7eb', dot: '#9ca3af' }
}

// ── summary row ───────────────────────────────────────────────────────────────
function SummaryPill({ status, count, selected, onClick }) {
  const m = statusMeta(status)
  const active = selected.length === 0 || selected.includes(status)
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '5px 12px', borderRadius: 100, cursor: 'pointer',
        border: `1px solid ${active ? m.border : '#e5e7eb'}`,
        background: active ? m.bg : '#f9fafb',
        color: active ? m.text : '#9ca3af',
        fontSize: 12, fontWeight: 600, transition: 'all .15s',
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%',
                     background: active ? m.dot : '#d1d5db', flexShrink: 0 }} />
      {m.label} <span style={{ opacity: .7 }}>{count}</span>
    </button>
  )
}

// ── item card ─────────────────────────────────────────────────────────────────
function ItemCard({ item }) {
  const [open, setOpen] = useState(false)
  const m = statusMeta(item.status)

  const extra = item.blocked_reason || item.decision_required ||
                item.next_action || item.notes || item.evidence

  return (
    <div style={{
      border: `1px solid ${m.border}`,
      borderRadius: 8,
      marginBottom: 6,
      background: m.bg,
      overflow: 'hidden',
    }}>
      <div
        onClick={() => extra && setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'flex-start', gap: 10,
          padding: '9px 12px',
          cursor: extra ? 'pointer' : 'default',
        }}
      >
        {/* ID chip */}
        <span style={{
          fontSize: 10, fontWeight: 700, fontFamily: 'monospace',
          padding: '2px 6px', borderRadius: 4,
          background: 'rgba(0,0,0,.06)', color: m.text,
          flexShrink: 0, marginTop: 1,
        }}>
          {item.id}
        </span>

        {/* Title */}
        <span style={{ fontSize: 13, fontWeight: 500, color: m.text, flex: 1, lineHeight: 1.4 }}>
          {item.title}
        </span>

        {/* Right: priority + status chip + expand arrow */}
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
          {item.priority && (
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '1px 5px', borderRadius: 3,
              background: 'rgba(0,0,0,.06)', color: m.text,
            }}>
              {item.priority}
            </span>
          )}
          {item.previously_missed && (
            <span title="Not on any earlier roadmap" style={{
              fontSize: 10, padding: '1px 5px', borderRadius: 3,
              background: '#fef9c3', color: '#854d0e', border: '1px solid #fde68a',
            }}>
              NEW
            </span>
          )}
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 100,
            border: `1px solid ${m.border}`, color: m.text, background: 'rgba(255,255,255,.5)',
          }}>
            {m.label}
          </span>
          {extra && (
            <span style={{ fontSize: 11, color: m.text, opacity: .6 }}>
              {open ? '▲' : '▼'}
            </span>
          )}
        </div>
      </div>

      {open && extra && (
        <div style={{
          padding: '0 12px 10px 12px',
          borderTop: `1px solid ${m.border}`,
          paddingTop: 8,
        }}>
          {item.blocked_reason && (
            <Row label="Blocked" value={item.blocked_reason} color={m.text} />
          )}
          {item.decision_required && (
            <Row label="Decision needed" value={item.decision_required} color={m.text} />
          )}
          {item.next_action && (
            <Row label="Next action" value={item.next_action} color={m.text} />
          )}
          {item.notes && (
            <Row label="Notes" value={item.notes} color={m.text} />
          )}
          {item.evidence && (
            <Row label="Evidence" value={item.evidence} color={m.text} />
          )}
          {item.completed_on && (
            <Row label="Completed" value={item.completed_on} color={m.text} />
          )}
          {item.depends_on && item.depends_on.length > 0 && (
            <Row label="Depends on" value={item.depends_on.join(', ')} color={m.text} />
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, color }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 5, fontSize: 12, lineHeight: 1.5 }}>
      <span style={{ color, fontWeight: 600, minWidth: 110, flexShrink: 0 }}>{label}</span>
      <span style={{ color, opacity: .8 }}>{value}</span>
    </div>
  )
}

// ── system group ──────────────────────────────────────────────────────────────
function SystemGroup({ system, items, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)

  const counts = {}
  for (const it of items) {
    counts[it.status] = (counts[it.status] || 0) + 1
  }
  const done = counts.COMPLETE || 0
  const pct = Math.round((done / items.length) * 100)

  return (
    <div style={{
      background: 'var(--god-card, #ffffff)',
      border: '1px solid var(--god-border, #e5e7eb)',
      borderRadius: 10,
      marginBottom: 12,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '12px 16px', cursor: 'pointer',
          borderBottom: open ? '1px solid var(--god-border, #e5e7eb)' : 'none',
          background: 'var(--god-card, #ffffff)',
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 700, flex: 1,
                       color: 'var(--god-text, #1f2937)' }}>
          {system}
        </span>

        {/* Mini status dots */}
        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          {Object.entries(counts).map(([s, n]) => {
            const m = statusMeta(s)
            return (
              <span key={s} title={`${m.label}: ${n}`} style={{
                fontSize: 10, fontWeight: 700, padding: '1px 6px',
                borderRadius: 100, background: m.bg, color: m.text,
                border: `1px solid ${m.border}`,
              }}>
                {n}
              </span>
            )
          })}
        </div>

        {/* Progress bar */}
        <div style={{ width: 80, height: 6, background: '#e5e7eb', borderRadius: 3, flexShrink: 0 }}>
          <div style={{
            width: `${pct}%`, height: '100%', background: '#22c55e',
            borderRadius: 3, transition: 'width .3s',
          }} />
        </div>
        <span style={{ fontSize: 11, color: 'var(--god-muted, #6b7280)', minWidth: 36 }}>
          {pct}%
        </span>
        <span style={{ fontSize: 13, color: 'var(--god-muted, #6b7280)' }}>
          {open ? '▲' : '▼'}
        </span>
      </div>

      {/* Items */}
      {open && (
        <div style={{ padding: '12px 16px' }}>
          {items.map(it => <ItemCard key={it.id} item={it} />)}
        </div>
      )}
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function GodRoadmapBoard() {
  const [statusFilter, setStatusFilter] = useState([])   // [] = show all
  const [systemFilter, setSystemFilter] = useState('')
  const [showMissed, setShowMissed]     = useState(false)
  const [search, setSearch]             = useState('')

  // All unique systems, in the order they first appear
  const allSystems = useMemo(() => {
    const seen = new Set()
    const out  = []
    for (const it of ALL_ITEMS) {
      if (it.system && !seen.has(it.system)) { seen.add(it.system); out.push(it.system) }
    }
    return out
  }, [])

  // Status summary counts across all items
  const allStatuses = useMemo(() => {
    const c = {}
    for (const it of ALL_ITEMS) c[it.status] = (c[it.status] || 0) + 1
    return c
  }, [])

  // Filtered items
  const filtered = useMemo(() => {
    let items = ALL_ITEMS
    if (statusFilter.length)  items = items.filter(it => statusFilter.includes(it.status))
    if (systemFilter)          items = items.filter(it => it.system === systemFilter)
    if (showMissed)            items = items.filter(it => it.previously_missed)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      items = items.filter(it =>
        it.id?.toLowerCase().includes(q) ||
        it.title?.toLowerCase().includes(q) ||
        it.notes?.toLowerCase().includes(q) ||
        it.next_action?.toLowerCase().includes(q)
      )
    }
    return items
  }, [statusFilter, systemFilter, showMissed, search])

  // Group filtered items by system, preserving discovery order
  const grouped = useMemo(() => {
    const map = {}
    for (const it of filtered) {
      if (!map[it.system]) map[it.system] = []
      map[it.system].push(it)
    }
    // Sort within each system by: COMPLETE last, priority, then id
    for (const sys of Object.keys(map)) {
      map[sys].sort((a, b) => {
        if (a.status === 'COMPLETE' && b.status !== 'COMPLETE') return  1
        if (b.status === 'COMPLETE' && a.status !== 'COMPLETE') return -1
        const pa = PRIORITY_ORDER[a.priority] ?? 99
        const pb = PRIORITY_ORDER[b.priority] ?? 99
        return pa - pb || (a.id > b.id ? 1 : -1)
      })
    }
    // Return in original system order
    return allSystems
      .filter(s => map[s]?.length)
      .map(s => ({ system: s, items: map[s] }))
  }, [filtered, allSystems])

  const totalComplete = ALL_ITEMS.filter(it => it.status === 'COMPLETE').length
  const totalPct      = Math.round((totalComplete / ALL_ITEMS.length) * 100)

  const toggleStatus = (s) =>
    setStatusFilter(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    )

  const pageStyle = {
    padding: '24px 32px',
    maxWidth: 1040,
    fontFamily: 'var(--god-font, system-ui, sans-serif)',
    color: 'var(--god-text, #1f2937)',
  }

  return (
    <div style={pageStyle}>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Platform Roadmap</h1>
        <p style={{ margin: '6px 0 0', color: 'var(--god-muted, #6b7280)', fontSize: 14 }}>
          {ALL_ITEMS.length} items · {allSystems.length} systems ·&nbsp;
          <strong style={{ color: '#166534' }}>{totalComplete} complete ({totalPct}%)</strong>
          &nbsp;· read-only — sourced from <code style={{ fontSize: 12 }}>platformRoadmap.json</code>
        </p>
      </div>

      {/* Overall progress bar */}
      <div style={{
        background: 'var(--god-card, #ffffff)',
        border: '1px solid var(--god-border, #e5e7eb)',
        borderRadius: 10, padding: '16px 20px', marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Overall completion</span>
          <span style={{ fontSize: 13, color: '#22c55e', fontWeight: 700 }}>{totalPct}%</span>
          <span style={{ fontSize: 12, color: 'var(--god-muted, #6b7280)' }}>
            {totalComplete} / {ALL_ITEMS.length}
          </span>
        </div>
        <div style={{ height: 8, background: '#e5e7eb', borderRadius: 4 }}>
          <div style={{
            width: `${totalPct}%`, height: '100%', background: '#22c55e',
            borderRadius: 4, transition: 'width .4s',
          }} />
        </div>
      </div>

      {/* Status filter pills */}
      <div style={{
        display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center',
      }}>
        <span style={{ fontSize: 12, color: 'var(--god-muted, #6b7280)', fontWeight: 600 }}>
          Filter:
        </span>
        {Object.entries(allStatuses)
          .sort((a, b) => {
            const order = ['COMPLETE','FINISH','BLOCKED','POLICY_REQUIRED','CONSOLIDATE','NOT_BUILT','REMOVE']
            return order.indexOf(a[0]) - order.indexOf(b[0])
          })
          .map(([s, n]) => (
            <SummaryPill
              key={s}
              status={s}
              count={n}
              selected={statusFilter}
              onClick={() => toggleStatus(s)}
            />
          ))
        }
        {statusFilter.length > 0 && (
          <button
            onClick={() => setStatusFilter([])}
            style={{
              fontSize: 11, padding: '4px 10px', borderRadius: 100,
              border: '1px solid #e5e7eb', background: '#fff', cursor: 'pointer',
              color: '#6b7280',
            }}
          >
            Clear
          </button>
        )}
      </div>

      {/* Second filter row */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          placeholder="Search by id, title, notes…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            fontSize: 13, padding: '6px 12px', borderRadius: 6,
            border: '1px solid var(--god-border, #e5e7eb)',
            background: 'var(--god-card, #fff)',
            color: 'var(--god-text, #1f2937)',
            width: 240,
          }}
        />

        <select
          value={systemFilter}
          onChange={e => setSystemFilter(e.target.value)}
          style={{
            fontSize: 13, padding: '6px 10px', borderRadius: 6,
            border: '1px solid var(--god-border, #e5e7eb)',
            background: 'var(--god-card, #fff)',
            color: 'var(--god-text, #1f2937)',
          }}
        >
          <option value="">All systems</option>
          {allSystems.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13,
                        color: 'var(--god-text, #1f2937)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showMissed}
            onChange={e => setShowMissed(e.target.checked)}
          />
          Previously missed only
        </label>

        <span style={{ fontSize: 12, color: 'var(--god-muted, #6b7280)', marginLeft: 'auto' }}>
          Showing {filtered.length} / {ALL_ITEMS.length}
        </span>
      </div>

      {/* Grouped system sections */}
      {grouped.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '48px 0',
          color: 'var(--god-muted, #9ca3af)', fontSize: 14,
        }}>
          No items match the current filters
        </div>
      ) : (
        grouped.map(({ system, items }) => (
          <SystemGroup
            key={system}
            system={system}
            items={items}
            defaultOpen={items.some(it => it.status !== 'COMPLETE')}
          />
        ))
      )}
    </div>
  )
}
