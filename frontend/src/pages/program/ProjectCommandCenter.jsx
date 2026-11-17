import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
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

function ProjectEditor({ project, busy, notice, onSave, onCancel }) {
  const dialog = useRef(null)
  useLayoutEffect(() => {
    const element = dialog.current
    const trigger = document.activeElement
    element.showModal()
    return () => {
      element.close()
      trigger?.focus({ preventScroll: true })
    }
  }, [])
  const [draft, setDraft] = useState(project)
  const patch = changes => setDraft(old => ({ ...old, ...changes }))
  const setTask = (index, change) => patch({
    tasks: draft.tasks.map((task, i) => i === index ? { ...task, ...change } : task),
  })
  const incomplete = draft.status === 'done' && draft.tasks.some(task => !task.done)
  return (
    <dialog ref={dialog} className="pcc-dialog" aria-labelledby="pcc-editor-title"
            onCancel={event => { event.preventDefault(); if (!busy) onCancel() }}>
      <form className="pcc-editor" onSubmit={e => { e.preventDefault(); onSave(draft) }}>
        <header className="pcc-editor-header">
          <div>
            <h2 id="pcc-editor-title">{project.id ? 'Edit project' : 'New project'}</h2>
            {project.id && <p>{project.name}</p>}
          </div>
          <button type="button" className="pcc-icon-button" aria-label="Close editor" disabled={busy}
                  onClick={onCancel}>×</button>
        </header>
        <div className="pcc-editor-body">
          {notice?.error && <p className="pcc-notice" role="alert">{notice.text}</p>}
          <fieldset disabled={busy}>
            {!project.id && <label>Project name
              <input autoFocus value={draft.name} onChange={e => patch({ name: e.target.value })}
                     required maxLength={200} />
            </label>}
            <label>Status
              <select autoFocus={!!project.id} value={draft.status} onChange={e => patch({ status: e.target.value })}>
                {Object.entries(STATUSES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
              </select>
            </label>
            <label>Notes
              <textarea value={draft.summary} onChange={e => patch({ summary: e.target.value })} maxLength={4000} rows={4} />
            </label>
            <fieldset className="pcc-editor-checklist">
              <legend>Checklist</legend>
              {draft.tasks.map((task, index) => (
                <div key={index} className="pcc-task-editor">
                  <input type="checkbox" aria-label={'Complete task ' + (index + 1)} checked={task.done}
                         onChange={e => setTask(index, { done: e.target.checked })} />
                  <input aria-label={'Task ' + (index + 1)} value={task.title} required maxLength={200}
                         onChange={e => setTask(index, { title: e.target.value })} />
                  <button type="button" className="pcc-icon-button" aria-label={'Remove task ' + (index + 1)}
                          onClick={() => patch({ tasks: draft.tasks.filter((_, i) => i !== index) })}>×</button>
                </div>
              ))}
              <button type="button" disabled={draft.tasks.length >= 100}
                      onClick={() => patch({ tasks: [...draft.tasks, { title: '', done: false }] })}>+ Add task</button>
            </fieldset>
            {incomplete && <p role="status">Complete the remaining tasks to mark this project done.</p>}
          </fieldset>
        </div>
        <footer className="pcc-editor-footer">
          <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
          <button className="pcc-primary" disabled={busy || incomplete}>{busy ? 'Saving…' : 'Save project'}</button>
        </footer>
      </form>
    </dialog>
  )
}

function ProjectCard({ project, busy, onEdit, onToggle }) {
  const done = project.tasks.filter(task => task.done).length
  const next = project.tasks.findIndex(task => !task.done)
  const progress = project.tasks.length ? Math.round(100 * done / project.tasks.length) : project.status === 'done' ? 100 : 0
  return (
    <article className="pcc-card" data-status={project.status} aria-label={project.name}>
      <div className="pcc-card-head">
        <span className="pcc-status">{STATUSES[project.status]}</span>
        <button type="button" className="pcc-edit-button" disabled={busy} onClick={onEdit}
                aria-label={'Edit ' + project.name}>Edit <span aria-hidden="true">↗</span></button>
      </div>
      <h2>{project.name}</h2>
      {project.summary && <p className="pcc-notes">{project.summary}</p>}
      <div className="pcc-card-progress">
        <div className="pcc-progress-label">
          <span>{project.tasks.length ? done + ' of ' + project.tasks.length + ' tasks complete' : 'No checklist tasks'}</span>
          <strong>{progress}%</strong>
        </div>
        <progress aria-label={project.name + ' progress'} value={progress} max={100} />
      </div>
      {!!project.tasks.length && (
        <ul className="pcc-tasks">
          {project.tasks.map((task, index) => (
            <li key={index} data-next={index === next} data-done={task.done}>
              <label><input type="checkbox" checked={task.done} disabled={busy}
                            onChange={e => onToggle(index, e.target.checked)} />
                <span>{task.title}{index === next && <small>Next task</small>}</span>
              </label>
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
      if (closeEditor && (!id || (filter !== 'all' && filter !== status))) setFilter(status)
      if (closeEditor && !id) setQuery('')
      return 'Project saved.'
    }, { closeEditor })
  }

  const projects = data?.projects || []
  const counts = Object.fromEntries(Object.keys(STATUSES).map(key => [key, 0]))
  for (const project of projects) counts[project.status] += 1
  const taskTotal = projects.reduce((total, project) => total + project.tasks.length, 0)
  const taskDone = projects.reduce((total, project) => total + project.tasks.filter(task => task.done).length, 0)
  const completion = taskTotal ? Math.round(100 * taskDone / taskTotal) : 0
  const normalizedQuery = query.trim().toLowerCase()
  const visible = projects.filter(project =>
    (filter === 'all' || project.status === filter) &&
    [project.name, project.summary, ...project.tasks.map(task => task.title)]
      .some(value => value.toLowerCase().includes(normalizedQuery)),
  )

  return (
    <main className="pcc">
      <header className="pcc-hero">
        <div className="pcc-hero-inner">
          <div className="pcc-masthead">
            <Link to="/workspaces">← Program Access</Link>
            <span>EvoSys <b>Pro</b></span>
          </div>
          <div className="pcc-hero-content">
            <div>
              <h1>Project<br />Command Center</h1>
              <button type="button" className="pcc-primary"
                      onClick={() => { setNotice(null); setEditing({ ...NEW_PROJECT, status: filter === 'wishlist' ? 'wishlist' : 'todo' }) }}
                      disabled={!data || busy}>+ Add project</button>
            </div>
            {data && <div className="pcc-overview">
              <div className="pcc-ring" style={{ '--completion': completion + '%' }} aria-hidden="true"><strong>{completion}%</strong></div>
              <div><h2>Checklist completion</h2><p>{taskDone} of {taskTotal} tasks complete</p></div>
            </div>}
          </div>
        </div>
      </header>
      <section className="pcc-board" aria-labelledby="pcc-board-title">
        {notice && !editing && <p className="pcc-notice" role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
        {data?.storage?.warning && <p role="alert">{data.storage.warning}</p>}
        {!data && (notice
          ? <button type="button" disabled={busy} onClick={() => run(async () => {}, () => 'Board refreshed.')}>Reload board</button>
          : <p role="status">Loading projects…</p>)}
        {data && <>
          <nav className="pcc-filters" aria-label="Project status">
            {[['all', 'All Projects'], ...Object.entries(STATUSES)].map(([key, label]) => (
              <button key={key} type="button" data-status={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                <span className="pcc-filter-label">{label}</span>
                <strong>{key === 'all' ? projects.length : counts[key]}</strong>
              </button>
            ))}
          </nav>
          <div className="pcc-toolbar">
            <h2 id="pcc-board-title">Projects <span>{visible.length}</span></h2>
            <input type="search" aria-label="Search projects and tasks" placeholder="Search projects and tasks"
                   value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          {!projects.length && <button type="button" disabled={busy}
                    onClick={() => run(() => api.post(ENDPOINT + '/starter', {}),
                      result => result.added ? 'Added ' + result.added + ' starter projects.' : 'Starter projects are already on the Launch Board.')}>
              Add starter projects
            </button>}
          <div className="pcc-grid">
            {visible.map(project => <ProjectCard key={project.id} project={project} busy={busy}
              onEdit={() => { setNotice(null); setEditing(project) }} onToggle={(index, done) => save({
                ...project,
                status: !done && project.status === 'done' ? 'in_progress' : project.status,
                tasks: project.tasks.map((task, i) => i === index ? { ...task, done } : task),
              })} />)}
          </div>
          {!visible.length && <p>No projects match this view.</p>}
        </>}
      </section>
      {editing && <ProjectEditor key={editing.id || 'new'} project={editing} busy={busy} notice={notice}
                                 onCancel={() => setEditing(null)} onSave={project => save(project, true)} />}
    </main>
  )
}
