/* Phone photos are 4-15 MB. Before an upload leaves the browser, a large
 * JPEG / PNG / WEBP photo is redrawn at most 2400 px on its long side as a
 * JPEG (quality 0.85): it still looks sharp on any screen, it uploads fast on
 * a field connection, and it does not fill the database. Everything else -
 * PDFs, Word files, CSVs, zips, HEIC (which browsers can't redraw) and small
 * images - is sent exactly as it was chosen. If shrinking fails for any
 * reason the original file is sent. */

const MAX_SIDE = 2400
const SHRINK_ABOVE = 1.5 * 1024 * 1024
const REDRAWABLE = ['image/jpeg', 'image/png', 'image/webp']

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = (e) => { URL.revokeObjectURL(url); reject(e) }
    img.src = url
  })
}

export async function shrinkImageFile(file) {
  try {
    if (!(file instanceof File) || !REDRAWABLE.includes(file.type) || file.size <= SHRINK_ABOVE) return file
    if (typeof document === 'undefined') return file
    const img = await loadImage(file)
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
    const w = Math.max(1, Math.round(img.naturalWidth * scale))
    const h = Math.max(1, Math.round(img.naturalHeight * scale))
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#fff' // a transparent PNG becomes a white-backed JPEG, not black
    ctx.fillRect(0, 0, w, h)
    ctx.drawImage(img, 0, 0, w, h)
    const blob = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.85))
    if (!blob || blob.size >= file.size) return file
    const name = file.name.replace(/\.(png|webp|jpe?g)$/i, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified })
  } catch {
    return file
  }
}

/** The same FormData, with any large photo in it shrunk. */
export async function shrinkFormData(formData) {
  if (typeof FormData === 'undefined' || !(formData instanceof FormData)) return formData
  let changed = false
  const out = new FormData()
  for (const [k, v] of formData.entries()) {
    if (v instanceof File) {
      const s = await shrinkImageFile(v)
      if (s !== v) changed = true
      out.append(k, s, s.name)
    } else {
      out.append(k, v)
    }
  }
  return changed ? out : formData
}

/** A data URL for a profile photo: redrawn at most `side` px, JPEG. */
export async function photoDataUrl(file, side = 900) {
  const img = await loadImage(file)
  const scale = Math.min(1, side / Math.max(img.naturalWidth, img.naturalHeight))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
  canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  return canvas.toDataURL('image/jpeg', 0.85)
}
