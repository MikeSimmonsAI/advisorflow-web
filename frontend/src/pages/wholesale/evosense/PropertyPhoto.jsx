/* A real street-level photo of THIS address (Google Street View), shown only
 * when Google reports imagery for that exact location, with its source and
 * date. Otherwise the plain "Property image unavailable" panel - never a stock
 * or "representative" house. Free Google Maps links are always offered. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { PropertyThumb } from '../ds/ds'

const WHY = {
  not_configured: 'No photo service is connected yet (a Google key with Street View is needed).',
  key_rejected: 'The Google key does not have the Street View Static API turned on.',
  no_imagery: 'Google has no street-level photo for this address.',
  no_address: 'No street address on file to photograph.',
  sandbox: 'Sandbox record - no photo.',
}

export function usePropertyPhoto(propertyId, { enabled = true, size = '640x400' } = {}) {
  const [state, setState] = useState({ src: null, meta: null, loading: !!enabled })
  useEffect(() => {
    if (!enabled || !propertyId) { setState({ src: null, meta: null, loading: false }); return undefined }
    let alive = true
    let url = null
    ;(async () => {
      try {
        const meta = await api.get(`/wholesale/evosense/properties/${propertyId}/photo`)
        if (alive && meta && meta.available) {
          const blob = await api.get(`/wholesale/evosense/properties/${propertyId}/photo.jpg?size=${size}`, { asBlob: true })
          url = URL.createObjectURL(blob)
        }
        if (alive) setState({ src: url, meta, loading: false })
      } catch {
        if (alive) setState({ src: null, meta: null, loading: false })
      }
    })()
    return () => { alive = false; if (url) URL.revokeObjectURL(url) }
  }, [propertyId, enabled, size])
  return state
}

export function photoCredit(meta) {
  if (!meta || !meta.available) return null
  return `Google Street View${meta.date ? ` · ${meta.date}` : ''}`
}

export function photoWhy(meta) {
  if (!meta) return null
  return WHY[meta.reason] || (meta.reason ? `No photo (${String(meta.reason).replace(/_/g, ' ')})` : null)
}

export default function PropertyPhoto({ propertyId, address, size = 'hero', isTest, showWhy = true }) {
  const { src, meta, loading } = usePropertyPhoto(propertyId, { enabled: !isTest })
  return (
    <>
      <PropertyThumb src={src} address={address} size={size} lazy={false} credit={photoCredit(meta)}
                     label={isTest ? 'Sandbox · property image unavailable' : loading ? 'Loading photo…' : undefined} />
      {showWhy && !loading && !src && meta && photoWhy(meta) ? (
        <p className="evo-muted evo-small" style={{ margin: '6px 0 0' }}>{photoWhy(meta)}</p>
      ) : null}
    </>
  )
}
