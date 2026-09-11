/**
 * THE CUSTOMER'S OWN VIEW OF THEIR COMMERCIAL ARRANGEMENT.
 *
 * WHAT THIS SCREEN IS NOT
 * -----------------------
 * It is not the internal console with the percentages hidden. It renders a
 * different payload — `/commercial/me` — which contains no allocation, no
 * party economics, no internal notes and no enum names. The customer cannot
 * see those things because they are not in the response, not because a flag
 * in here decided not to draw them.
 *
 * THE LANGUAGE RULE
 * -----------------
 * Nothing on this page says "basis enum", "allocation rule" or
 * "terms_required". The customer is asked business questions in business
 * words: what the percentage applies to, which collections count, who gets
 * paid first, how often settlement happens. Every label and option comes from
 * the server's question definitions, so a brand that rewords a question
 * reworders it everywhere at once.
 *
 * INCOMPLETE IS A NORMAL STATE HERE.
 * An unanswered question is shown as an open question, not as an error and not
 * as something blocking their onboarding — because it does not block their
 * onboarding. The rest of the launch continues while these are settled.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

function Field({ question, value, onChange, disabled }) {
  const kind = question.kind

  if (kind === 'select') {
    return (
      <select
        className="os-input"
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">Not answered yet</option>
        {(question.allowed_values || []).map((opt) => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    )
  }

  if (kind === 'money') {
    // Stored in cents, typed in dollars. Empty is UNANSWERED and 0 is an
    // answer — the two are deliberately different values here as well.
    return (
      <input
        className="os-input"
        type="number"
        min="0"
        step="0.01"
        disabled={disabled}
        value={value === null || value === undefined ? '' : (value / 100)}
        placeholder="Not answered yet"
        onChange={(e) => {
          const raw = e.target.value
          onChange(raw === '' ? null : Math.round(parseFloat(raw) * 100))
        }}
      />
    )
  }

  if (kind === 'textarea') {
    return (
      <textarea
        className="os-input"
        rows={3}
        disabled={disabled}
        value={value ?? ''}
        placeholder="Not answered yet"
        onChange={(e) => onChange(e.target.value || null)}
      />
    )
  }

  return (
    <input
      className="os-input"
      disabled={disabled}
      value={value ?? ''}
      placeholder="Not answered yet"
      onChange={(e) => onChange(e.target.value || null)}
    />
  )
}

export default function MyCommercial() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [savingKey, setSavingKey] = useState(null)
  const [drafts, setDrafts] = useState({})

  function load() {
    setLoading(true)
    api.get('/commercial/me')
      .then((body) => { setData(body); setDrafts({}) })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function saveAnswer(question) {
    const value = question.key in drafts ? drafts[question.key] : question.value
    setSavingKey(question.key)
    setError('')
    setSuccess('')
    try {
      // `revision` is sent back exactly as it was read. If somebody else
      // answered the same question in between, the server refuses with a 409
      // rather than quietly overwriting their answer.
      const body = await api.put(`/commercial/me/questions/${question.key}`, {
        value,
        revision: question.revision,
      })
      setData(body)
      setDrafts({})
      setSuccess('Saved.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingKey(null)
    }
  }

  if (loading) return <div className="panel os-section">Loading…</div>

  if (!data?.has_agreement) {
    return (
      <div className="panel os-section">
        <div className="panel-header"><h2 className="panel-title">Commercial agreement</h2></div>
        <p className="os-hint">{data?.status_line || 'Nothing to show yet.'}</p>
      </div>
    )
  }

  const open = data.questions.filter((q) => !q.answered)
  const answered = data.questions.filter((q) => q.answered)

  return (
    <div className="os-grid" style={{ gridTemplateColumns: '1fr' }}>
      <section className="panel os-section">
        <div className="panel-header">
          <h2 className="panel-title">Commercial agreement</h2>
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 18, fontWeight: 700 }}>{data.headline}</span>
          {data.effective_date && (
            <span className="os-hint">Effective {data.effective_date}</span>
          )}
        </div>
        <p className="os-hint" style={{ marginTop: 6 }}>{data.status_line}</p>

        {open.length > 0 && (
          <div className="os-hint" style={{
            marginTop: 8, padding: '8px 10px', borderRadius: 8,
            background: 'rgba(47,182,255,0.08)',
            border: '1px solid rgba(47,182,255,0.25)',
          }}>
            {open.length} question{open.length === 1 ? '' : 's'} still to answer.
            The rest of your setup carries on in the meantime — none of it is
            waiting on these.
          </div>
        )}

        {error && <div className="os-error">{error}</div>}
        {success && <div className="os-success">{success}</div>}
      </section>

      {[['Still to answer', open], ['Agreed', answered]].map(([title, rows]) => (
        rows.length === 0 ? null : (
          <section className="panel os-section" key={title}>
            <div className="panel-header"><h2 className="panel-title">{title}</h2></div>
            {rows.map((question) => (
              <label className="os-label" key={question.key}>
                {question.label}
                {question.description && (
                  <span className="os-hint">{question.description}</span>
                )}
                <Field
                  question={question}
                  value={question.key in drafts ? drafts[question.key] : question.value}
                  disabled={savingKey === question.key}
                  onChange={(v) => setDrafts((d) => ({ ...d, [question.key]: v }))}
                />
                {question.help_text && (
                  <span className="os-hint">{question.help_text}</span>
                )}
                <button
                  className="btn btn--secondary"
                  style={{ fontSize: 12, padding: '4px 12px', marginTop: 6 }}
                  disabled={savingKey === question.key || !(question.key in drafts)}
                  onClick={() => saveAnswer(question)}
                >
                  {savingKey === question.key ? 'Saving…' : 'Save answer'}
                </button>
              </label>
            ))}
          </section>
        )
      ))}

      {data.document?.has_document && (
        <section className="panel os-section">
          <div className="panel-header"><h2 className="panel-title">Agreement document</h2></div>
          <p className="os-hint">
            {data.document.reference || 'A signed agreement is on file.'}
          </p>
        </section>
      )}
    </div>
  )
}
