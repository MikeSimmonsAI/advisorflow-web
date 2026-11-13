import { useEffect, useState } from 'react'
import { api, fetchObjectUrl } from '../../api/client'

function errorText(error) {
  return error?.response?.data?.detail || error?.message || 'Request failed.'
}

export function ApplicantExport({ orgId, filters, options }) {
  const [columns, setColumns] = useState([])
  const [name, setName] = useState('')
  const [presets, setPresets] = useState(options.presets)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const base = `/volt/ops/${orgId}`
  const keys = Object.keys(options.columns)
  const selected = columns.length ? columns : keys

  async function run(save) {
    setBusy(true)
    setMessage('')
    try {
      if (save) {
        const preset = await api.put(`${base}/export-options/presets`, { name, columns: selected })
        setPresets((previous) => [...previous.filter((item) => item.name !== preset.name), preset])
        setMessage('Preset saved.')
      } else {
        const query = new URLSearchParams(filters)
        selected.forEach((key) => query.append('column', key))
        const url = await fetchObjectUrl(`${base}/export.csv?${query}`)
        const link = document.createElement('a')
        link.href = url
        link.download = 'volt-marketing-applications.csv'
        link.click()
        setTimeout(() => URL.revokeObjectURL(url), 2000)
      }
    } catch (error) {
      setMessage(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  if (!keys.length) return null
  return <details className="v-panel">
    <summary>Configure applicant CSV</summary>
    <p>Choose fields in the order you want them exported.</p>
    <button type="button" onClick={() => setColumns(keys)}>Select all</button>
    <button type="button" onClick={() => setColumns([])}>Reset order</button>
    <div className="v-filters">{keys.map((key) => <label key={key}>
      <input type="checkbox" checked={columns.includes(key)} onChange={(event) =>
        setColumns(event.target.checked ? [...columns, key] : columns.filter((field) => field !== key))} />
      {options.columns[key]}{columns.includes(key) && ` (${columns.indexOf(key) + 1})`}
    </label>)}</div>
    <label>Saved preset <select value="" onChange={(event) => {
      const preset = presets.find((item) => item.name === event.target.value)
      if (preset) { setColumns(preset.columns); setName(preset.name) }
    }}><option value="">Choose preset</option>{presets.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>
    <label>Preset name <input maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <button type="button" className="v-btn" disabled={busy || !name.trim()} onClick={() => run(true)}>Save preset</button>
    <button type="button" className="v-btn v-btn--black" disabled={busy} onClick={() => run(false)}>Download CSV</button>
    {message && <p role="status">{message}</p>}
  </details>
}

export function useApplicantExport(orgId) {
  const [options, setOptions] = useState(null)
  useEffect(() => {
    let current = true
    api.get(`/volt/ops/${orgId}/export-options`).then((value) => {
      if (current) setOptions(value)
    }).catch(() => {})
    return () => { current = false }
  }, [orgId])
  return options
}

export function AcaApplicants({ orgId }) {
  const options = useApplicantExport(orgId)
  const [filters, setFilters] = useState({ status: '', date_from: '', date_to: '' })
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  useEffect(() => {
    let current = true
    api.get(`/volt/ops/${orgId}/applications`, { params: filters }).then((data) => {
      if (current) { setItems(data.items); setError('') }
    }).catch((problem) => {
      if (current) { setItems([]); setError(errorText(problem)) }
    })
    return () => { current = false }
  }, [orgId, filters])
  const columns = Object.entries(options?.view_columns || {})
  return <section>
    <h2>ACA applicants</h2>
    <div className="v-filters">
      {options?.view_columns?.status_label && <label>Stage <select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value })}>
        <option value="">All stages</option>
        {['draft', 'submitted', 'needs_update', 'approved', 'started', 'withdrawn'].map((stage) =>
          <option key={stage} value={stage}>{stage.replace('_', ' ')}</option>)}
      </select></label>}
      {options?.view_columns?.created_at && Object.entries({ date_from: 'Started from', date_to: 'Started through' }).map(([key, label]) =>
        <label key={key}>{label}<input type="date" value={filters[key]}
          onChange={(event) => setFilters({ ...filters, [key]: event.target.value })} /></label>)}
    </div>
    {error && <p role="alert">{error}</p>}
    {options && <ApplicantExport orgId={orgId} filters={filters} options={options} />}
    {columns.length > 0 && <div style={{ overflowX: 'auto' }}><table className="v-table">
      <thead><tr>{columns.map(([key, label]) => <th key={key} scope="col">{label}</th>)}</tr></thead>
      <tbody>{items.map((row, index) => <tr key={index}>{columns.map(([key]) => <td key={key}>{row[key]}</td>)}</tr>)}</tbody>
    </table>{items.length === 0 && <p>No applicants match these filters.</p>}</div>}
  </section>
}

export function AcaAccessSettings({ orgId }) {
  const options = useApplicantExport(orgId)
  const [grants, setGrants] = useState([])
  const [form, setForm] = useState({ email: '', active: true, view_fields: [], export_fields: [] })
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const url = `/volt/ops/${orgId}/aca-access`
  useEffect(() => {
    let current = true
    api.get(url).then((data) => { if (current) setGrants(data.items) })
      .catch((error) => { if (current) setMessage(errorText(error)) })
    return () => { current = false }
  }, [url])

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    try {
      const saved = await api.put(url, form)
      setGrants((previous) => [...previous.filter((grant) => grant.email !== saved.email), saved])
      setForm(saved)
      setMessage('ACA access saved.')
    } catch (error) { setMessage(errorText(error)) } finally { setBusy(false) }
  }

  return <details className="v-panel"><summary>ACA-only staff access</summary>
    <form onSubmit={save}>
      <label>Existing staff login <input type="email" required value={form.email}
        onChange={(event) => {
          const email = event.target.value
          setForm(grants.find((grant) => grant.email.toLowerCase() === email.toLowerCase()) ||
            { email, active: true, view_fields: [], export_fields: [] })
        }} /></label>
      <label>Saved access <select value="" onChange={(event) => {
        const grant = grants.find((item) => item.email === event.target.value)
        if (grant) setForm(grant)
      }}><option value="">Choose staff login</option>{grants.map((grant) =>
        <option key={grant.email} value={grant.email}>{grant.email}{grant.active ? '' : ' (inactive)'}</option>)}</select></label>
      <label><input type="checkbox" checked={form.active} onChange={(event) =>
        setForm({ ...form, active: event.target.checked })} /> Active</label>
      <div className="v-filters">{Object.entries(options?.aca_fields || {}).map(([key, label]) => <fieldset key={key}>
        <legend>{label}</legend>
        <label><input type="checkbox" checked={form.view_fields.includes(key)} onChange={(event) =>
          setForm(event.target.checked ? { ...form, view_fields: [...form.view_fields, key] } :
            { ...form, view_fields: form.view_fields.filter((field) => field !== key),
              export_fields: form.export_fields.filter((field) => field !== key) })} /> View</label>
        <label><input type="checkbox" checked={form.export_fields.includes(key)} disabled={!form.view_fields.includes(key)}
          onChange={(event) => setForm({ ...form, export_fields: event.target.checked ?
            [...form.export_fields, key] : form.export_fields.filter((field) => field !== key) })} /> Export</label>
      </fieldset>)}</div>
      <button className="v-btn" disabled={busy || !options} type="submit">Save ACA access</button>
      {message && <p role="status">{message}</p>}
    </form>
  </details>
}
