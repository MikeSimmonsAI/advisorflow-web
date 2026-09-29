// Plays a voicemail through the AUTHENTICATED proxy (GET /voicemails/{id}/audio).
// The provider's recording URL never reaches the browser; the bytes are
// fetched with the user's token and handed to <audio> as an object URL.
import { useEffect, useState } from 'react'
import { fetchObjectUrl } from '../../api/client'
import './telephony.css'

export default function VoicemailPlayer({ path }) {
  const [url, setUrl] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => () => { if (url) URL.revokeObjectURL(url) }, [url])
  if (!path) return <span className="tel-note">No recording stored.</span>
  if (url) return <audio className="tel-audio" controls autoPlay src={url} />
  return (
    <span className="tel-row">
      <button type="button" className="tel-btn" disabled={busy} onClick={async () => {
        setBusy(true); setErr('')
        try { setUrl(await fetchObjectUrl(path)) } catch (e) { setErr(e.message || 'Could not load the recording.') }
        finally { setBusy(false) }
      }}>{busy ? 'Loading…' : 'Play voicemail'}</button>
      {err && <span className="tel-note">{err}</span>}
    </span>
  )
}
