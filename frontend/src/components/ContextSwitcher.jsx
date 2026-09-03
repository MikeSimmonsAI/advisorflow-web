/**
 * ContextSwitcher — moving between the back office and a customer workspace.
 *
 * THE RULE THIS RENDERS, AND WHERE IT COMES FROM
 *
 * Every button here is derived from ONE server response, /auth/my-contexts.
 * Nothing on this screen is decided from a role label, from
 * users.organization_id, from localStorage, or from a hardcoded organization
 * name. A workspace that is not in that response does not exist as far as this
 * component is concerned.
 *
 *   in the back office, 0 workspaces   render nothing
 *   in the back office, 1 workspace    [ WORKSPACE ]
 *   in the back office, 2+ workspaces  [ WORKSPACES v ]
 *   in a workspace, has back office    [ BACK OFFICE ]
 *   in a workspace, no back office     render nothing
 *
 * AND THE BUTTON IS NOT THE CONTROL. Hiding it is UX. Every route behind it
 * re-checks the membership server-side, so typing /workspace/<someone-elses-id>
 * gets a 403 from a person who never saw a button. The two answers are
 * independent on purpose - that is the only arrangement where the UI being
 * wrong is a cosmetic bug rather than a breach.
 */
import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchMyContexts, setWorkspaceContext, clearWorkspaceContext,
         getWorkspaceContext } from '../api/client'

export default function ContextSwitcher({ current = 'back_office' }) {
  const [contexts, setContexts] = useState(null)
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const boxRef = useRef(null)

  useEffect(function () {
    let live = true
    fetchMyContexts()
      .then(function (data) { if (live) setContexts(data) })
      // A failure here renders NOTHING rather than guessing. A switcher that
      // falls back to a locally-derived list is exactly the thing this
      // component exists to not be.
      .catch(function () { if (live) setContexts(null) })
    return function () { live = false }
  }, [])

  useEffect(function () {
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return function () { document.removeEventListener('mousedown', onDocClick) }
  }, [])

  if (!contexts) return null

  const workspaces    = contexts.workspace_contexts || []
  const executives    = contexts.executive_contexts || []
  const hasBackOffice = !!contexts.has_back_office
  const hasExecutive  = executives.length > 0

  function enterWorkspace(ws) {
    setOpen(false)
    // The selection is stored FIRST so the very next request already carries
    // the header - otherwise the workspace screen's first load would resolve
    // against the previous context and render the wrong tenant for one frame.
    setWorkspaceContext(ws.organization_id)
    navigate('/workspace/' + ws.organization_id)
  }

  function enterExecutive() {
    setOpen(false)
    navigate('/executive')
  }

  function backToOffice() {
    setOpen(false)
    // Leaving the workspace clears the selection. A stale header would keep
    // scoping back-office requests to a customer the user has walked out of.
    clearWorkspaceContext()
    navigate('/sales')
  }

  // ── INSIDE A WORKSPACE ───────────────────────────────────────────────────
  if (current === 'workspace') {
    if (!hasBackOffice) return null           // a customer's own staff stay put
    return (
      <button type="button" className="ctx-switch-btn" onClick={backToOffice}
              title="Return to the sales back office">
        ← Back Office
      </button>
    )
  }

  // ── INSIDE THE BACK OFFICE ───────────────────────────────────────────────
  // Executive Suite link — only when the server has confirmed an executive
  // grant. Never derived from role label, never hardcoded.
  if (current === 'back_office') {
    if (workspaces.length === 0 && !hasExecutive) return null

    // If only an executive link and no workspaces, show a single button.
    if (workspaces.length === 0 && hasExecutive) {
      return (
        <button type="button" className="ctx-switch-btn" onClick={enterExecutive}
                title="Executive Suite">
          Executive Suite
        </button>
      )
    }

    // Mixed: workspaces + possibly executive. Render a dropdown.
    if (workspaces.length === 1 && !hasExecutive) {
      const ws = workspaces[0]
      return (
        <button type="button" className="ctx-switch-btn"
                onClick={function () { enterWorkspace(ws) }}
                title={'Enter ' + ws.organization_name}>
          Workspace
        </button>
      )
    }

    // Dropdown for multiple workspaces and/or executive.
    const activeId = getWorkspaceContext()
    return (
      <div className="ctx-switch-wrap" ref={boxRef}>
        <button type="button" className="ctx-switch-btn"
                aria-haspopup="menu" aria-expanded={open}
                onClick={function () { setOpen(!open) }}>
          Switch View <span className="ctx-switch-caret">▾</span>
        </button>
        {open && (
          <div className="ctx-switch-menu" role="menu">
            {hasExecutive && (
              <>
                <div className="ctx-switch-menu-head">Executive</div>
                {executives.map(function (ex) {
                  return (
                    <button key={ex.platform_id} type="button" role="menuitem"
                            className={'ctx-switch-item' +
                                       (current === 'executive' ? ' is-active' : '')}
                            onClick={enterExecutive}>
                      <span className="ctx-switch-item-name">{ex.platform_name} — Executive Suite</span>
                    </button>
                  )
                })}
              </>
            )}
            {workspaces.length > 0 && (
              <>
                <div className="ctx-switch-menu-head">Workspaces</div>
                {workspaces.map(function (ws) {
                  return (
                    <button key={ws.organization_id} type="button" role="menuitem"
                            className={'ctx-switch-item' +
                                       (ws.organization_id === activeId ? ' is-active' : '')}
                            onClick={function () { enterWorkspace(ws) }}>
                      <span className="ctx-switch-item-name">{ws.organization_name}</span>
                      {/* The WORKSPACE role, which is not this person's platform
                          role and is never derived from it. */}
                      <span className="ctx-switch-item-role">{ws.role}</span>
                    </button>
                  )
                })}
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  return null
}
