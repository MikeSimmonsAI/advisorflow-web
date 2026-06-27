import { useEffect, useRef, useState } from 'react'
import './RichEmailComposer.css'

const MAX_IMAGE_BYTES = 1_500_000 // ~1.5MB - generous for a promo flyer, conservative enough to avoid bloating the email past most providers' size limits once base64-encoded (base64 inflates size by ~33%)

/**
 * A real, lightweight content composer for email - per Mike's direct,
 * repeated complaint that the Email Queue wasn't useful as-is, and his
 * own reasoning for why: promos and visual content perform better by
 * email than a one-line text. The old editor was a raw textarea
 * editing HTML by hand - no formatting controls, no way to actually
 * add an image at all.
 *
 * DELIBERATELY NOT a third-party rich-text library (no new dependency
 * added) - contentEditable plus a small toolbar covers the actual
 * requirement (bold/italic text, embedded images) without the weight
 * of a full editor framework. Images are embedded as base64 data URIs
 * directly in the HTML, not uploaded to separate file storage - this
 * means BOTH existing send paths (SendGrid, Microsoft Graph) work with
 * zero code changes, since they already just take a body_html string;
 * an embedded <img src="data:..."> is invisible to them as anything
 * other than ordinary HTML content.
 */
export default function RichEmailComposer({ value, onChange }) {
  const editorRef = useRef(null)
  const fileInputRef = useRef(null)
  const [imageError, setImageError] = useState('')

  // Set the editor's initial content ONCE on mount, directly via the
  // DOM, rather than via React's dangerouslySetInnerHTML on every
  // render. contentEditable content is mutated by the browser as the
  // user types - if value changes after mount (which it will, via our
  // own onChange below) and React re-applies dangerouslySetInnerHTML
  // on a re-render, it would reset the DOM content out from under the
  // user mid-edit, fighting their cursor position. This way the DOM is
  // only ever written to once, then the user (and our own image-insert
  // logic) owns it directly.
  useEffect(() => {
    if (editorRef.current) {
      editorRef.current.innerHTML = value || ''
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function exec(command) {
    document.execCommand(command)
    editorRef.current?.focus()
    onChange(editorRef.current?.innerHTML || '')
  }

  function handleInput() {
    onChange(editorRef.current?.innerHTML || '')
  }

  function handleImageButtonClick() {
    setImageError('')
    fileInputRef.current?.click()
  }

  function handleImageSelected(event) {
    const file = event.target.files?.[0]
    event.target.value = '' // allow selecting the same file again later
    if (!file) return

    if (!file.type.startsWith('image/')) {
      setImageError('Please choose an image file.')
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setImageError(`That image is too large (max ${Math.round(MAX_IMAGE_BYTES / 1_000_000 * 10) / 10}MB). Try a smaller or more compressed version.`)
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const img = document.createElement('img')
      img.src = reader.result
      img.style.maxWidth = '100%'
      img.style.display = 'block'
      img.style.margin = '8px 0'

      editorRef.current?.focus()
      const selection = window.getSelection()
      if (selection && selection.rangeCount > 0 && editorRef.current?.contains(selection.anchorNode)) {
        const range = selection.getRangeAt(0)
        range.collapse(false)
        range.insertNode(img)
      } else {
        editorRef.current?.appendChild(img)
      }
      handleInput()
    }
    reader.onerror = () => setImageError('Could not read that image file.')
    reader.readAsDataURL(file)
  }

  return (
    <div className="rich-email-composer">
      <div className="rich-email-toolbar">
        <button type="button" className="rich-email-toolbar-btn" onClick={() => exec('bold')} title="Bold">
          <strong>B</strong>
        </button>
        <button type="button" className="rich-email-toolbar-btn" onClick={() => exec('italic')} title="Italic">
          <em>I</em>
        </button>
        <button type="button" className="rich-email-toolbar-btn" onClick={() => exec('underline')} title="Underline">
          <u>U</u>
        </button>
        <span className="rich-email-toolbar-divider" />
        <button type="button" className="rich-email-toolbar-btn" onClick={() => exec('insertUnorderedList')} title="Bullet list">
          • List
        </button>
        <span className="rich-email-toolbar-divider" />
        <button type="button" className="rich-email-toolbar-btn rich-email-toolbar-btn--image" onClick={handleImageButtonClick} title="Insert image (flyer, promo graphic, etc.)">
          🖼 Add image
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleImageSelected}
          style={{ display: 'none' }}
        />
      </div>

      {imageError && <div className="compose-error rich-email-image-error">{imageError}</div>}

      <div
        ref={editorRef}
        className="rich-email-editor"
        contentEditable
        suppressContentEditableWarning
        onInput={handleInput}
      />
    </div>
  )
}
