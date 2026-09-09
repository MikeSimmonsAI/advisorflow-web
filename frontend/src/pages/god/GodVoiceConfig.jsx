/**
 * GodVoiceConfig — Voice agent configuration screen.
 *
 * VOICE-03: surfaces all voice endpoints that were previously curl-only:
 *   GET  /god/voice/agents            — list mappings with readiness
 *   POST /god/voice/agents            — create a new mapping
 *   PATCH /god/voice/agents/:id/version — pin agent version
 *   PATCH /god/voice/agents/:id/attempt-policy — use-case attempt policy
 *   PATCH /god/orgs/:id/attempt-policy — org-level attempt policy
 *   POST /god/voice/test-call         — place test call (with confirmation)
 *
 * Rule: never shows api_key values. Reports key presence only.
 * Rule: test call requires explicit confirm — one extra click, no accident.
 */
import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'

const USE_CASES = ['file_check', 'appointment_reminder', 'reengagement']
const USE_CASE_LABELS = {
  file_check:           'File Check Call',
  appointment_reminder: 'Appointment Reminder',
  reengagement:         'Re-engagement Call',
}
const PROVIDERS = ['retell']

function ReadinessChip({ ready, why }) {
  if (ready === null || ready === undefined) return (
    <span style={{ fontSize: 11, color: '#9ca3af' }}>checking…</span>
  )
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 100,
      background: ready ? '#f0fdf4' : '#fef2f2',
      color: ready ? '#166534' : '#991b1b',
      border: `1px solid ${ready ? '#86efac' : '#fca5a5'}`,
    }}>
      {ready ? '✓ Ready' : `✗ ${why || 'Not ready'}`}
    </span>
  )
}

function AgentCard({ agent, onVersionSave, onAttemptPolicySave, onTestCall }) {
  const [versionEdit, setVersionEdit] = useState(false)
  const [versionVal, setVersionVal] = useState(String(agent.agent_version ?? ''))
  const [versionSaving, setVersionSaving] = useState(false)

  const [policyEdit, setPolicyEdit] = useState(false)
  const [maxCall, setMaxCall] = useState(String(agent.max_call_attempts ?? ''))
  const [maxDial, setMaxDial] = useState(String(agent.max_dial_attempts ?? ''))
  const [policySaving, setPolicySaving] = useState(false)

  const [testConfirm, setTestConfirm] = useState(false)
  const [testLeadId, setTestLeadId] = useState('')
  const [testCalling, setTestCalling] = useState(false)
  const [testResult, setTestResult] = useState(null)

  const saveVersion = async () => {
    const v = parseInt(versionVal, 10)
    if (isNaN(v) || v < 0) { alert('Enter a non-negative integer version number.'); return }
    setVersionSaving(true)
    try {
      await onVersionSave(agent.id, v)
      setVersionEdit(false)
    } finally { setVersionSaving(false) }
  }

  const savePolicy = async () => {
    setPolicySaving(true)
    try {
      const payload = {}
      if (maxCall) payload.max_call_attempts = parseInt(maxCall, 10)
      if (maxDial) payload.max_dial_attempts = parseInt(maxDial, 10)
      await onAttemptPolicySave(agent.id, payload)
      setPolicyEdit(false)
    } finally { setPolicySaving(false) }
  }

  const placeCall = async () => {
    if (!testLeadId.trim()) { alert('Enter a lead ID first.'); return }
    setTestCalling(true); setTestResult(null)
    try {
      const r = await onTestCall(agent.organization_id, testLeadId.trim(), agent.use_case)
      setTestResult({ ok: true, data: r })
    } catch (e) {
      setTestResult({ ok: false, msg: e.detail || e.message })
    } finally { setTestCalling(false); setTestConfirm(false) }
  }

  const fieldStyle = {
    fontSize: 12, padding: '4px 8px', borderRadius: 6,
    border: '1px solid #e5e7eb', width: 90,
  }
  const btnStyle = (color = '#3b82f6') => ({
    fontSize: 12, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
    background: color, color: '#fff', border: 'none', fontWeight: 600,
  })
  const ghostBtn = {
    fontSize: 12, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
    background: 'transparent', color: '#6b7280',
    border: '1px solid #e5e7eb',
  }

  return (
    <div style={{
      border: '1px solid #e5e7eb', borderRadius: 10, padding: '18px 22px',
      marginBottom: 14, background: '#fff',
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>
            {agent.organization_name || agent.organization_id}
          </div>
          <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>
            {agent.provider} · {USE_CASE_LABELS[agent.use_case] || agent.use_case}
            {agent.label && <span> · {agent.label}</span>}
          </div>
          <div style={{ fontSize: 11, fontFamily: 'monospace', color: '#9ca3af', marginTop: 4 }}>
            agent: {agent.agent_id}
            {' · '}from: {agent.from_number}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end' }}>
          <ReadinessChip ready={agent.provider_ready} why={agent.provider_not_ready_reason} />
          <span style={{ fontSize: 11, color: agent.is_active ? '#22c55e' : '#ef4444' }}>
            {agent.is_active ? '● active' : '○ inactive'}
          </span>
          <span style={{ fontSize: 11, color: agent.org_api_key_override ? '#3b82f6' : '#9ca3af' }}>
            API key: {agent.org_api_key_override ? 'custom' : 'platform default'}
          </span>
        </div>
      </div>

      {/* Version section */}
      <div style={{ marginTop: 14, borderTop: '1px solid #f3f4f6', paddingTop: 12 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#6b7280', minWidth: 100 }}>Agent version</span>
          {!versionEdit ? (
            <>
              <span style={{ fontSize: 13, fontWeight: 600,
                             color: agent.version_pinned ? '#1f2937' : '#f59e0b' }}>
                {agent.version_pinned ? `v${agent.agent_version}` : 'unpinned — cannot call'}
              </span>
              <button style={ghostBtn} onClick={() => { setVersionVal(String(agent.agent_version ?? '')); setVersionEdit(true) }}>
                {agent.version_pinned ? 'Change' : 'Pin version'}
              </button>
            </>
          ) : (
            <>
              <input type="number" min={0} value={versionVal}
                     onChange={e => setVersionVal(e.target.value)}
                     style={{ ...fieldStyle, width: 70 }} />
              <button style={btnStyle()} onClick={saveVersion} disabled={versionSaving}>
                {versionSaving ? 'Saving…' : 'Save'}
              </button>
              <button style={ghostBtn} onClick={() => setVersionEdit(false)}>Cancel</button>
            </>
          )}
        </div>
      </div>

      {/* Attempt policy section */}
      <div style={{ marginTop: 10, borderTop: '1px solid #f3f4f6', paddingTop: 12 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#6b7280', minWidth: 100 }}>Attempt policy</span>
          {!policyEdit ? (
            <>
              <span style={{ fontSize: 12, color: '#6b7280' }}>
                max calls: {agent.max_call_attempts ?? 'default'} ·
                max dials: {agent.max_dial_attempts ?? 'default'}
              </span>
              <button style={ghostBtn} onClick={() => setPolicyEdit(true)}>Edit</button>
            </>
          ) : (
            <>
              <label style={{ fontSize: 12, color: '#6b7280' }}>max calls</label>
              <input type="number" min={1} value={maxCall} onChange={e => setMaxCall(e.target.value)}
                     style={fieldStyle} placeholder="default" />
              <label style={{ fontSize: 12, color: '#6b7280' }}>max dials</label>
              <input type="number" min={1} value={maxDial} onChange={e => setMaxDial(e.target.value)}
                     style={fieldStyle} placeholder="default" />
              <button style={btnStyle()} onClick={savePolicy} disabled={policySaving}>
                {policySaving ? 'Saving…' : 'Save'}
              </button>
              <button style={ghostBtn} onClick={() => setPolicyEdit(false)}>Cancel</button>
            </>
          )}
        </div>
      </div>

      {/* Test call section */}
      <div style={{ marginTop: 10, borderTop: '1px solid #f3f4f6', paddingTop: 12 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: '#6b7280', minWidth: 100 }}>Test call</span>
          {!testConfirm ? (
            <button style={ghostBtn} onClick={() => setTestConfirm(true)}
                    disabled={!agent.version_pinned}>
              {agent.version_pinned ? 'Place test call…' : 'Pin version first'}
            </button>
          ) : (
            <>
              <input type="text" value={testLeadId}
                     onChange={e => setTestLeadId(e.target.value)}
                     placeholder="Lead ID"
                     style={{ ...fieldStyle, width: 200, fontFamily: 'monospace' }} />
              <button style={btnStyle('#dc2626')} onClick={placeCall} disabled={testCalling}>
                {testCalling ? 'Calling…' : 'Confirm call'}
              </button>
              <button style={ghostBtn} onClick={() => { setTestConfirm(false); setTestResult(null) }}>
                Cancel
              </button>
            </>
          )}
        </div>
        {testResult && (
          <div style={{
            marginTop: 8, fontSize: 12, padding: '8px 12px', borderRadius: 6,
            background: testResult.ok ? '#f0fdf4' : '#fef2f2',
            color: testResult.ok ? '#166534' : '#991b1b',
            fontFamily: 'monospace', whiteSpace: 'pre-wrap',
          }}>
            {testResult.ok
              ? `Call placed: ${testResult.data?.call_id || ''} · status: ${testResult.data?.status || ''}`
              : `Refused: ${testResult.msg}`}
          </div>
        )}
      </div>
    </div>
  )
}

function CreateAgentForm({ orgs, onCreated }) {
  const [orgId, setOrgId] = useState('')
  const [agentId, setAgentId] = useState('')
  const [fromNumber, setFromNumber] = useState('')
  const [provider, setProvider] = useState('retell')
  const [useCase, setUseCase] = useState('file_check')
  const [label, setLabel] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  const submit = async () => {
    if (!orgId || !agentId || !fromNumber) {
      setErr('Organization, Agent ID, and From Number are required.'); return
    }
    setSaving(true); setErr(null)
    try {
      const r = await api.post('/god/voice/agents', {
        organization_id: orgId, agent_id: agentId, from_number: fromNumber,
        provider, use_case: useCase, label: label || null,
      })
      onCreated(r)
    } catch (e) {
      setErr(e.detail || e.message)
    } finally { setSaving(false) }
  }

  const fieldRow = { display: 'flex', gap: 10, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }
  const lbl = { fontSize: 12, color: '#6b7280', minWidth: 120 }
  const inp = { fontSize: 12, padding: '5px 8px', borderRadius: 6, border: '1px solid #e5e7eb', flex: 1, minWidth: 200 }
  const sel = { ...inp, flex: 'none', minWidth: 140 }

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: '18px 22px',
                  background: '#fafafa', marginBottom: 20 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 14 }}>Add Voice Agent Mapping</div>
      {err && <div style={{ background: '#fef2f2', color: '#dc2626', borderRadius: 6,
                            padding: '8px 12px', fontSize: 12, marginBottom: 12 }}>{err}</div>}
      <div style={fieldRow}>
        <span style={lbl}>Organization</span>
        <select value={orgId} onChange={e => setOrgId(e.target.value)} style={sel}>
          <option value="">Select org…</option>
          {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
      </div>
      <div style={fieldRow}>
        <span style={lbl}>Agent ID</span>
        <input value={agentId} onChange={e => setAgentId(e.target.value)}
               placeholder="agent_xxx (from Retell dashboard)" style={{ ...inp, fontFamily: 'monospace' }} />
      </div>
      <div style={fieldRow}>
        <span style={lbl}>From number</span>
        <input value={fromNumber} onChange={e => setFromNumber(e.target.value)}
               placeholder="+15550001234" style={{ ...inp, fontFamily: 'monospace' }} />
      </div>
      <div style={fieldRow}>
        <span style={lbl}>Provider</span>
        <select value={provider} onChange={e => setProvider(e.target.value)} style={sel}>
          {PROVIDERS.map(p => <option key={p}>{p}</option>)}
        </select>
        <span style={lbl}>Use case</span>
        <select value={useCase} onChange={e => setUseCase(e.target.value)} style={sel}>
          {USE_CASES.map(u => <option key={u} value={u}>{USE_CASE_LABELS[u] || u}</option>)}
        </select>
      </div>
      <div style={fieldRow}>
        <span style={lbl}>Label (optional)</span>
        <input value={label} onChange={e => setLabel(e.target.value)}
               placeholder="human-readable label" style={inp} />
      </div>
      <button onClick={submit} disabled={saving} style={{
        fontSize: 13, padding: '7px 20px', borderRadius: 7, border: 'none',
        background: '#3b82f6', color: '#fff', cursor: 'pointer', fontWeight: 600,
      }}>
        {saving ? 'Creating…' : 'Create mapping'}
      </button>
    </div>
  )
}

export default function GodVoiceConfig() {
  const [agents, setAgents] = useState([])
  const [orgs, setOrgs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [refreshed, setRefreshed] = useState(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [aRes, oRes] = await Promise.all([
        api.get('/god/voice/agents'),
        api.get('/god/orgs', { params: { limit: 200 } }),
      ])
      setAgents(aRes?.agents || [])
      setOrgs(oRes?.orgs || [])
      setRefreshed(new Date())
    } catch (e) {
      setError(e.detail || e.message || 'Failed to load voice config')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handleVersionSave = async (configId, version) => {
    await api.patch(`/god/voice/agents/${configId}/version`, { agent_version: version })
    await load()
  }

  const handleAttemptPolicy = async (configId, payload) => {
    await api.patch(`/god/voice/agents/${configId}/attempt-policy`, payload)
    await load()
  }

  const handleTestCall = async (orgId, leadId, useCase) => {
    const r = await api.post('/god/voice/test-call', {
      organization_id: orgId, lead_id: leadId, use_case: useCase,
    })
    return r
  }

  const handleCreated = () => { setShowCreate(false); load() }

  const page = { padding: '24px 32px', maxWidth: 960,
                  fontFamily: 'var(--god-font, system-ui, sans-serif)',
                  color: 'var(--god-text, #1f2937)' }

  return (
    <div style={page}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Voice Configuration</h1>
          <p style={{ margin: '6px 0 0', color: '#6b7280', fontSize: 14 }}>
            Agent mappings, version pins, attempt policy, and test calls — god-only
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowCreate(s => !s)} style={{
            fontSize: 12, padding: '6px 14px', borderRadius: 6,
            border: 'none', background: '#3b82f6', color: '#fff', cursor: 'pointer',
          }}>
            {showCreate ? '✕ Cancel' : '+ Add mapping'}
          </button>
          <button onClick={load} disabled={loading} style={{
            fontSize: 12, padding: '6px 14px', borderRadius: 6,
            border: '1px solid #e5e7eb', background: '#fff', cursor: 'pointer',
          }}>
            ↻ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8,
                      padding: 16, color: '#dc2626', marginBottom: 20 }}>
          {error}
        </div>
      )}

      {showCreate && <CreateAgentForm orgs={orgs} onCreated={handleCreated} />}

      {loading && !agents.length && (
        <div style={{ color: '#9ca3af', padding: 24 }}>Loading…</div>
      )}

      {!loading && agents.length === 0 && !showCreate && (
        <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: '32px 24px',
                      textAlign: 'center', color: '#9ca3af', fontSize: 14 }}>
          No voice agent mappings yet.
          <br />
          <button onClick={() => setShowCreate(true)} style={{
            marginTop: 12, fontSize: 13, padding: '7px 18px', borderRadius: 7,
            border: 'none', background: '#3b82f6', color: '#fff', cursor: 'pointer',
          }}>
            Add first mapping
          </button>
        </div>
      )}

      {agents.map(a => (
        <AgentCard key={a.id} agent={a}
          onVersionSave={handleVersionSave}
          onAttemptPolicySave={handleAttemptPolicy}
          onTestCall={handleTestCall}
        />
      ))}

      {refreshed && (
        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 8 }}>
          As of {refreshed.toLocaleTimeString()} · {agents.length} mapping{agents.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  )
}
