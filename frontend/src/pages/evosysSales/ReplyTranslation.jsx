import { useState } from 'react'
import { api } from '../../api/client'

export function useReplyTranslation({ leadId, message, body, spanishDraft, englishDraft }) {
  const [preference, setPreference] = useState(null)
  const [preview, setPreview] = useState(null)
  const language = preference && preference.messageId === message?.id
    ? preference.language : message?.translation ? 'Spanish' : 'English'
  const translation = preview?.messageId === message?.id && preview?.source === body
    ? preview.body : null
  const needsPreview = language === 'Spanish' && body !== spanishDraft && !translation

  const choose = language => {
    setPreference({ messageId: message?.id, language })
    setPreview(null)
  }
  const prepare = async () => {
    if (!needsPreview) return language === 'Spanish' ? translation || body : body === spanishDraft ? englishDraft : body
    const result = await api.post(`/sales/evosys/leads/${leadId}/translate-reply`, { body })
    setPreview({ source: body, messageId: message?.id, body: result.body })
    return null
  }
  return { language, choose, translation, needsPreview, prepare }
}

export function ReplyTranslation({ reply, disabled }) {
  return (
    <div>
      <label>Send in{' '}
        <select value={reply.language} onChange={e => reply.choose(e.target.value)} disabled={disabled}>
          <option>English</option>
          <option>Spanish</option>
        </select>
      </label>
      {reply.language === 'Spanish' && reply.translation && (
        <div aria-live="polite"><b>Spanish preview</b><p>{reply.translation}</p></div>
      )}
    </div>
  )
}
