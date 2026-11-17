import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { BOARD_URL, errorText, isStale, newRequestKey, withExpectedVersion, writeHeaders } from '../../utils/launchBoard'
import './ProjectCommandCenter.css'

const ENDPOINT = BOARD_URL + '/checklist'
const STATUSES = {
  todo: 'To Do',
  in_progress: 'In Progress',
  done: 'Done',
  wishlist: 'Wish List',
}
const NEW_PROJECT = { name: '', summary: '', status: 'todo', tasks: [] }

function ProjectEditor({ project, busy, onSave, onCancel }) {
  const [draft, setDraft] = useState(project)
  const patch = changes => setDraft(old => ({ ...old, ...changes }))
  const setTask = (index, change) => patch({
    tasks: draft.tasks.map((task, i) => i === index ? { ...task, ...change } : task),
  })

  return (
    <form className="pcc-editor" onSubmit={e => { e.preventDefault(); onSave(draft) }}>
      <fieldset disabled={busy}>
        <legend>{project.id ? 'Edit project' : 'New project'}</legend>
        <label>Project name
          <input value={draft.name} onChange={e => patch({ name: e.target.value })}
                 readOnly={!!project.id} required maxLength={200} />
        </label>
        <label>Status
          <select value={draft.status} onChange={e => patch({ status: e.target.value })}>
            {Object.entries(STATUSES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select>
        </label>
        <label>Notes
          <textarea value={draft.summary} onChange={e => patch({ summary: e.target.value })} maxLength={4000} rows={3} />
        </label>
        <fieldset>
          <legend>Checklist</legend>
          {draft.tasks.map((task, index) => (
            <div key={index} className="pcc-task-editor">
              <input aria-label={'Task ' + (index + 1)} value={task.title} required maxLength={200}
                     onChange={e => setTask(index, { title: e.target.value })} />
              <button type="button" aria-label={'Remove task ' + (index + 1)}
                      onClick={() => patch({ tasks: draft.tasks.filter((_, i) => i !== index) })}>Remove</button>
            </div>
          ))}
          <button type="button" disabled={draft.tasks.length >= 100}
                  onClick={() => patch({ tasks: [...draft.tasks, { title: '', done: false }] })}>Add task</button>
        </fieldset>
        {draft.status === 'done' && draft.tasks.some(task => !task.done) &&
          <p>Check off the remaining tasks before marking this project done.</p>}
        <div className="pcc-actions">
          <button className="pcc-primary" disabled={draft.status === 'done' && draft.tasks.some(task => !task.done)}>
            Save project
          </button>
          <button type="button" onClick={onCancel}>Cancel</button>
        </div>
      </fieldset>
    </form>
  )
}

function ProjectCard({ project, busy, onEdit, onToggle }) {
  const done = project.tasks.filter(task => task.done).length
  const progress = project.tasks.length ? Math.round(100 * done / project.tasks.length) : project.status === 'done' ? 100 : 0
  return (
    <article className="pcc-card">
      <div className="pcc-card-head">
        <div><span className="pcc-status" data-status={project.status}>{STATUSES[project.status]}</span><h2>{project.name}</h2></div>
        <button type="button" disabled={busy} onClick={onEdit} aria-label={'Edit ' + project.name}>Edit</button>
      </div>
      {project.summary && <p className="pcc-notes">{project.summary}</p>}
      <div className="pcc-progress-label">
        <span>{project.tasks.length ? done + ' of ' + project.tasks.length + ' tasks complete' : 'No checklist tasks'}</span>
        <strong>{progress}%</strong>
      </div>
      <progress aria-label={project.name + ' progress'} value={progress} max={100} />
      {!!project.tasks.length && (
        <ul>
          {project.tasks.map((task, index) => (
            <li key={index}>
              <label><input type="checkbox" checked={task.done} disabled={busy}
                            onChange={e => onToggle(index, e.target.checked)} />{task.title}</label>
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}

export default function ProjectCommandCenter() {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const reload = useCallback(async () => {
    const result = await api.get(ENDPOINT, { noOrgContext: true })
    if (!Array.isArray(result?.projects)) throw new Error('Project data is unavailable.')
    setData(result)
  }, [])

  useEffect(() => {
    reload().catch(error => setNotice({ error: true, text: errorText(error) }))
  }, [reload])

  async function run(action, message, { closeEditor = false } = {}) {
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      if (closeEditor) setEditing(null)
      try {
        await reload()
        setNotice({ text: message(result) })
      } catch {
        setData(null)
        setNotice({ error: true, text: 'Saved. Reload the board to see the latest project data.' })
      }
    } catch (error) {
      if (isStale(error)) {
        try { await reload() } catch { setData(null) }
        setEditing(null)
        setNotice({ error: true, text: 'The board changed while you were editing. Reopen the project and try again.' })
      } else {
        setNotice({ error: true, text: errorText(error) })
      }
    } finally {
      setBusy(false)
    }
  }

  function save(project, closeEditor = false) {
    const { name, summary, status, tasks, id } = project
    const body = withExpectedVersion({ name, summary, status, tasks }, project)
    const url = id ? ENDPOINT + '/' + id + '/save' : ENDPOINT
    return run(() => api.post(url, body, writeHeaders(newRequestKey())), () => {
      if (closeEditor) {
        setFilter(status)
        setQuery('')
      }
      return 'Project saved.'
    }, { closeEditor })
  }

  const projects = data?.projects || []
  const counts = Object.fromEntries(Object.keys(STATUSES).map(key => [key, 0]))
  for (const project of projects) counts[project.status] += 1
  const normalizedQuery = query.trim().toLowerCase()
  const visible = projects.filter(project =>
    (filter === 'all' || project.status === filter) &&
    [project.name, project.summary, ...project.tasks.map(task => task.title)]
      .some(value => value.toLowerCase().includes(normalizedQuery)),
  )

  return (
    <main className="pcc">
      <header className="pcc-header">
        <div>
          <Link to="/workspaces">← Program Access</Link>
          <h1>Project Command Center</h1>
        </div>
        <div className="pcc-actions">
          <button type="button" onClick={() => setEditing({ ...NEW_PROJECT, status: filter === 'wishlist' ? 'wishlist' : 'todo' })}
                  className="pcc-primary" disabled={!data || busy || !!editing}>Add project</button>
        </div>
      </header>
      {notice && <p className="pcc-notice" role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
      {data?.storage?.warning && <p role="alert">{data.storage.warning}</p>}
      {!data && (notice
        ? <button type="button" disabled={busy} onClick={() => run(async () => {}, () => 'Board refreshed.')}>Reload board</button>
        : <p role="status">Loading projects…</p>)}
      {data && <>
        <nav className="pcc-filters" aria-label="Project status">
          {[['all', 'All Projects'], ...Object.entries(STATUSES)].map(([key, label]) => (
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              <strong>{key === 'all' ? projects.length : counts[key]}</strong>{label}
            </button>
          ))}
        </nav>
        <div className="pcc-toolbar">
          <input type="search" aria-label="Search projects and tasks" placeholder="Search projects and tasks"
                 value={query} onChange={e => setQuery(e.target.value)} />
          <button type="button" disabled={busy || !!editing}
                  onClick={() => run(() => api.post(ENDPOINT + '/starter', {}),
                    result => result.added ? 'Added ' + result.added + ' starter projects.' : 'Starter projects are already on the Launch Board.')}>
            Add starter projects
          </button>
        </div>
        {editing && <ProjectEditor key={editing.id || 'new'} project={editing} busy={busy} onCancel={() => setEditing(null)}
                                   onSave={project => save(project, true)} />}
        <div className="pcc-grid">
          {visible.map(project => <ProjectCard key={project.id} project={project} busy={busy || !!editing}
            onEdit={() => setEditing(project)} onToggle={(index, done) => save({
              ...project,
              status: !done && project.status === 'done' ? 'in_progress' : project.status,
              tasks: project.tasks.map((task, i) => i === index ? { ...task, done } : task),
            })} />)}
        </div>
        {!visible.length && <p>No projects match this view.</p>}
      </>}
    </main>
  )
}
