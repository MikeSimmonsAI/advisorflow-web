/**
 * Publish a prospect demo onto our own domain, from the deal it belongs to.
 *
 * The backend for this has existed since demo sites were built â€” publish, list
 * and revoke were all there. What was missing was any way to reach it without
 * an API client, so every demo had to be posted by hand and the link pasted
 * into the deal by hand after that.
 *
 * TWO SLOTS, NOT TWO VERSIONS. A deal can carry a platform walkthrough and a
 * website concept at the same time; they are different artifacts, so
 * publishing one never touches the other's link. Only the platform slot is
 * "this deal's demo" â€” publishing a website concept deliberately does not mark
 * the demo build ready, because it would tell the pipeline something untrue.
 *
 * REPUBLISHING MINTS A NEW TOKEN. The old link keeps working unless "retire
 * the current link" is ticked, which is the honest default: a prospect who
 * already has a link should not find it dead because somebody fixed a typo.
 * When it IS retired, the old URL stops opening immediately.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Card, Chip, Empty, ErrorBar, dateTime } from './parts'

const SLOTS = [
  ['platform', 'Platform walkthrough',
   "The product, running their workflow. This is the deal's demo."],
  ['website', 'Website concept',
   'An optional site design. Does not claim the demo slot.'],
]

// Matches the server's cap, so an oversized file is refused here with a
// sentence instead of a 400 after the upload.
const MAX_HTML_BYTES = 2 * 1024 * 1024

export default function DemoSitesPanel({ opp, onChanged }) {
  const [demos, setDemos] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(null)

  const [slot, setSlot] = useState('platform')
  const [title, setTitle] = useState('')
  const [html, setHtml] = useState('')
  const [fileName, setFileName] = useState('')
  const [retire, setRetire] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/sales/opportunities/${opp.id}/demo-sites`)
      setDemos(r.demos || [])
      setError(null)
    } catch (e) {
      setError(e?.message || 'Could not load demo sites')
    }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  function pickFile(e) {
    const f = e.target.files && e.target.files[0]
    if (!f) return
    if (f.size > MAX_HTML_BYTES) {
      setError(`That file is ${(f.size / 1024 / 1024).toFixed(1)}MB. `
               + 'The limit is 2MB â€” inline images as data URIs sparingly.')
      e.target.value = ''
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      setHtml(String(reader.result || ''))
      setFileName(f.name)
      setError(null)
      // A sensible default the rep can overwrite: the prospect sees this in
      // the browser tab, so it should read like a name, not a filename.
      if (!title) {
        const t = String(reader.result || '').match(/<title[^>]*>([^<]{1,120})<\/title>/i)
        setTitle(t ? t[1].trim() : f.name.replace(/\.html?$/i, ''))
      }
    }
    reader.onerror = () => setError('Could not read that file.')
    reader.readAsText(f)
  }

  async function publish() {
    if (!title.trim()) { setError('Give it a title â€” the prospect sees it.'); return }
    if (!html.trim()) { setError('Choose the HTML file first.'); return }
    setBusy(true); setError(null)
    try {
      const r = await api.post(`/sales/opportunities/${opp.id}/demo-site`, {
        title: title.trim(), html, slot, retire_previous: retire,
      })
      setOpen(false)
      setHtml(''); setFileName(''); setTitle(''); setRetire(false)
      await load()
      if (onChanged) onChanged()
      if (r && r.url) copy(r.url)
    } catch (e) {
      setError(e?.message || 'Publish failed')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(d) {
    // Irreversible for the prospect holding that link, so it asks first.
    if (!window.confirm(
      `Kill this link now?\n\n${d.url || d.title}\n\n`
      + 'Anyone who already has it will get "no longer available" '
      + 'the next time they open it.')) return
    setBusy(true); setError(null)
    try {
      await api.post(`/sales/demo-sites/${d.id}/revoke`)
      await load()
      if (onChanged) onChanged()
    } catch (e) {
      setError(e?.message || 'Could not revoke')
    } finally {
      setBusy(false)
    }
  }

  function copy(url) {
    if (!url) return
    try {
      navigator.clipboard.writeText(url)
      setCopied(url)
      setTimeout(() => setCopied(c => (c === url ? null : c)), 2500)
    } catch {
      // Clipboard can be blocked; the link is on screen and selectable.
      setCopied(null)
    }
  }

  const live = (demos || []).filter(d => d.is_live)
  const past = (demos || []).filter(d => !d.is_live)

  return (
    <Card title="DEMO SITES"
          sub="Publish a mockup on our own domain and send the prospect a link"
          right={live.length
            ? <Chip tone="green">{live.length} live</Chip>
            : <Chip>none live</Chip>}>

      <ErrorBar error={error} onRetry={load} />

      {demos === null ? <div className="sw-subtle">Loadingâ€¦</div> : null}

      {demos !== null && live.length === 0 && past.length === 0 ? (
        <Empty title="No demo published yet">
          Build the HTML, publish it here, and the prospect gets a link on our
          domain that you can revoke when the deal closes either way.
        </Empty>
      ) : null}

      {live.map(d => (
        <div key={d.id} className="sw-pcard sw-mt">
          <div className="sw-flex" style={{ justifyContent: 'space-between', gap: 12 }}>
            <div style={{ minWidth: 0 }}>
              <b>{d.title}</b>
              <div className="sw-subtle">
                {SLOTS.find(s => s[0] === (d.slot || 'platform'))?.[1] || d.slot}
                {' Â· '}
                {d.view_count
                  ? `${d.view_count} view${d.view_count === 1 ? '' : 's'}`
                  : 'not opened yet'}
                {d.last_viewed_at ? ` Â· last ${dateTime(d.last_viewed_at)}` : ''}
                {d.expires_at ? ` Â· expires ${dateTime(d.expires_at)}` : ''}
              </div>
            </div>
            <div className="sw-flex" style={{ gap: 8, flex: '0 0 auto' }}>
              <button className="sw-btn" onClick={() => copy(d.url)}>
                {copied === d.url ? 'Copied' : 'Copy link'}
              </button>
              <a className="sw-btn" href={d.url} target="_blank" rel="noreferrer">Open</a>
              <button className="sw-btn sw-ghost" disabled={busy}
                      onClick={() => revoke(d)}>Revoke</button>
            </div>
          </div>
          <div className="sw-subtle sw-mt"
               style={{ wordBreak: 'break-all', fontFamily: 'monospace', fontSize: 12 }}>
            {d.url}
          </div>
        </div>
      ))}

      {past.length ? (
        <div className="sw-subtle sw-mt">
          {past.length} retired link{past.length === 1 ? '' : 's'} â€” no longer opens.
        </div>
      ) : null}

      {!open ? (
        <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
          <button className="sw-btn sw-primary" onClick={() => setOpen(true)}>
            Publish a demo
          </button>
        </div>
      ) : (
        <div className="sw-pcard sw-mt">
          <div className="sw-field">
            <label>WHAT IS THIS</label>
            <select className="sw-select" value={slot}
                    onChange={e => setSlot(e.target.value)}>
              {SLOTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <div className="sw-subtle">{SLOTS.find(s => s[0] === slot)?.[2]}</div>
          </div>

          <div className="sw-field">
            <label>TITLE</label>
            <input className="sw-input" value={title}
                   placeholder="What the prospect sees in the browser tab"
                   onChange={e => setTitle(e.target.value)} />
          </div>

          <div className="sw-field">
            <label>HTML FILE</label>
            <input className="sw-input" type="file" accept=".html,.htm,text/html"
                   onChange={pickFile} />
            {fileName ? (
              <div className="sw-subtle">
                {fileName} Â· {(new Blob([html]).size / 1024).toFixed(0)}KB
              </div>
            ) : (
              <div className="sw-subtle">
                One self-contained file. It renders in a sandboxed frame, so
                in-page <code>#anchor</code> links must be handled by the page
                itself.
              </div>
            )}
          </div>

          <label className="sw-flex sw-mt" style={{ gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={retire}
                   onChange={e => setRetire(e.target.checked)} />
            <span>
              Retire the current link in this slot
              <span className="sw-subtle">
                {' '}â€” leave off and any link the prospect already has keeps working
              </span>
            </span>
          </label>

          <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end', gap: 8 }}>
            <button className="sw-btn" disabled={busy}
                    onClick={() => { setOpen(false); setError(null) }}>Cancel</button>
            <button className="sw-btn sw-primary" disabled={busy} onClick={publish}>
              {busy ? 'Publishingâ€¦' : 'Publish and copy link'}
            </button>
          </div>
        </div>
      )}
    </Card>
  )
}
