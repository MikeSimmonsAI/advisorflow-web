/**
 * Video meeting (Zoom) configuration status.
 *
 * NOT a settings screen — Checkpoint 4 does not build one. This answers the one
 * question a person actually has when a booked meeting has no Zoom link: is it
 * us, or is it them?
 *
 * "Test connection" performs a REAL round-trip. A green tick that only proves a
 * row exists in our own database is worth nothing, and the exact failure this
 * guards against is a UI claiming CONFIGURED while every meeting silently
 * fails to provision.
 *
 * No credential is ever shown, because none is ever sent.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import { Card, Chip, ErrorBar, dateTime } from './parts'

const STATE = {
  ready:          { tone: 'green', text: 'Ready' },
  not_configured: { tone: 'amber', text: 'Not configured' },
  error:          { tone: 'red',   text: 'Error' },
}

export default function VideoStatus() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (verify) => {
    setError(null)
    try { setData(await api.get('/sales/video/status' + (verify ? '?verify=true' : ''))) }
    catch (e) { setError(e.message || 'Could not read the video configuration.') }
  }, [])

  useEffect(() => { load(false) }, [load])

  async function test() {
    setBusy(true)
    await load(true)
    setBusy(false)
  }

  if (!data) {
    return <Card title="VIDEO MEETINGS"><div className="sw-subtle">Loading…</div></Card>
  }

  const s = STATE[data.state] || { tone: null, text: data.state }
  const withVideo = (data.meeting_types || []).filter(t => t.requires_video)

  return (
    <Card title="VIDEO MEETINGS"
          sub={data.provider_label + ' — meetings are created automatically'}
          right={<Chip tone={s.tone}>{s.text}</Chip>}>
      <ErrorBar error={error} onRetry={() => load(false)} />

      {data.detail && (
        <div className="sw-subtle"
             style={{ marginBottom: 10, color: data.state === 'ready' ? '#047857' : '#b45309' }}>
          {data.detail}
        </div>
      )}

      {data.state === 'ready' && (
        <>
          <div className="sw-flex sw-between" style={{ padding: '5px 0' }}>
            <span className="sw-subtle">Credentials</span>
            <span style={{ fontSize: 11 }}>
              {data.credential_source === 'brand_config'
                ? 'Configured for this brand'
                : 'From the server environment'}
            </span>
          </div>
          {/* Worth stating plainly: an env-var setup is shared platform-wide
              and will not scale to a second brand. */}
          {data.credential_source === 'environment' && (
            <div className="sw-subtle" style={{ marginTop: 4 }}>
              Shared across every brand on this server. A second brand needs its
              own credentials before it can host its own meetings.
            </div>
          )}
          {data.last_verified_at && (
            <div className="sw-flex sw-between" style={{ padding: '5px 0' }}>
              <span className="sw-subtle">Last verified</span>
              <span style={{ fontSize: 11 }}>{dateTime(data.last_verified_at)}</span>
            </div>
          )}
        </>
      )}

      {data.setup_hint && (
        <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                      borderRadius: 8, padding: 10, marginTop: 8 }}>
          <div className="sw-subtle">{data.setup_hint}</div>
        </div>
      )}

      <div className="sw-flex" style={{ gap: 8, marginTop: 10 }}>
        <button className="sw-tiny" onClick={test} disabled={busy}>
          {busy ? 'Checking…' : 'Test connection'}
        </button>
      </div>

      {/* "Zoom is ready" and "my Discovery call has no link" look
          contradictory until you can see which types actually ask for video. */}
      <div style={{ marginTop: 14, paddingTop: 10, borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 11 }}>MEETING TYPES WITH VIDEO</b>
        {withVideo.length === 0 ? (
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            None. No meeting will create a video room.
          </div>
        ) : (
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            {withVideo.map(t => t.name).join(' · ')}
          </div>
        )}
        {(data.meeting_types || []).some(t => !t.requires_video) && (
          <div className="sw-subtle" style={{ marginTop: 4 }}>
            No video:{' '}
            {(data.meeting_types || []).filter(t => !t.requires_video)
              .map(t => t.name).join(' · ')}
          </div>
        )}
      </div>
    </Card>
  )
}
