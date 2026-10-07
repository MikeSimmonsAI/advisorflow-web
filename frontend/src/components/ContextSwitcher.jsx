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
import { fetchMyContexts, switchWorkspace, clearWorkspaceContext,
         getWorkspaceContext, setBrandContext, getBrandContext } from '../api/client'
import { switchTargets } from '../auth/workspaceLanding'

// `workspacesOnly`: render only the switch between this person's customer
// workspaces (used beside WorkspaceAdminMenu, which already owns Back Office).
export default function ContextSwitcher({ current = 'back_office', workspacesOnly = false }) {
  const [contexts, setContexts] = useState(null)
  // 'loading' | 'ready' | 'error'. A failed load is NOT "no workspaces": it
  // keeps its own state so it can say so and offer a retry.
  const [phase, setPhase] = useState('loading')
  const [attempt, setAttempt] = useState(0)
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const boxRef = useRef(null)

  useEffect(function () {
    let live = true
    setPhase('loading')
    fetchMyContexts({ force: attempt > 0 })
      .then(function (data) { if (live) { setContexts(data); setPhase('ready') } })
      // A failure never falls back to a locally-derived list - that is exactly
      // the thing this component exists to not be. It also does not pose as
      // "no workspaces": it says the list is unavailable and offers a retry.
      .catch(function () { if (live) { setContexts(null); setPhase('error') } })
    return function () { live = false }
  }, [attempt])

  useEffect(function () {
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return function () { document.removeEventListener('mousedown', onDocClick) }
  }, [])

  if (phase === 'error') {
    return (
      <button type="button" className="ctx-switch-btn" role="alert"
              title="Your workspace list could not be loaded. Click to retry."
              onClick={function () { setAttempt(attempt + 1) }}>
        Workspaces unavailable - retry
      </button>
    )
  }
  if (!contexts) return null

  const workspaces    = contexts.workspace_contexts || []
  const executives    = contexts.executive_contexts || []
  const hasBackOffice = !!contexts.has_back_office && !workspacesOnly
  const hasExecutive  = executives.length > 0

  function enterWorkspace(ws) {
    setOpen(false)
    // The selection is stored FIRST so the very next request already carries
    // the header - otherwise the workspace screen's first load would resolve
    // against the previous context and render the wrong tenant for one frame.
    switchWorkspace(ws.organization_id)
    navigate('/workspace/' + ws.organization_id)
  }

  function enterExecutive(platformId, platformName) {
    setOpen(false)
    // Set brand context BEFORE navigating so the very first executive API
    // request already carries X-Brand-Override. This is required for god_admin
    // root authority: god selects an explicit brand context so executive queries
    // remain scoped to exactly one brand. For normal brand_executive users the
    // header is still sent but the backend ignores it (membership determines
    // scope). Never enters /executive without an explicit brand selection.
    if (platformId) {
      setBrandContext(platformId, platformName || '')
    }
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
    // OTHER workspaces this person holds, from the server's list only. A
    // customer manager with several memberships and no back office used to get
    // nothing here and could not move without editing the URL.
    const others = switchTargets(contexts, getWorkspaceContext())
    if (!hasBackOffice && others.length === 0) return null   // a customer's own staff stay put
    if (others.length === 0) {
      return (
        <button type="button" className="ctx-switch-btn" onClick={backToOffice}
                title="Return to the sales back office">
          ← Back Office
        </button>
      )
    }
    return (
      <div className="ctx-switch-wrap" ref={boxRef}>
        <button type="button" className="ctx-switch-btn"
                aria-haspopup="menu" aria-expanded={open}
                onClick={function () { setOpen(!open) }}>
          Switch Workspace <span className="ctx-switch-caret">▾</span>
        </button>
        {open && (
          <div className="ctx-switch-menu" role="menu">
            {hasBackOffice && (
              <button type="button" role="menuitem" className="ctx-switch-item"
                      onClick={backToOffice}>
                <span className="ctx-switch-item-name">← Back Office</span>
              </button>
            )}
            <div className="ctx-switch-menu-head">Workspaces</div>
            {others.map(function (ws) {
              return (
                <button key={ws.organization_id} type="button" role="menuitem"
                        className="ctx-switch-item"
                        onClick={function () { enterWorkspace(ws) }}>
                  <span className="ctx-switch-item-name">{ws.organization_name}</span>
                  <span className="ctx-switch-item-role">{ws.role}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  // ── INSIDE THE BACK OFFICE ───────────────────────────────────────────────
  // Executive Suite link — only when the server has confirmed an executive
  // grant. Never derived from role label, never hardcoded.
  if (current === 'back_office') {
    if (workspaces.length === 0 && !hasExecutive) return null

    // If only an executive link and no workspaces, resolve which executive to enter.
    //
    // BRAND CONTEXT RULE:
    //   If the user (e.g. god_admin) has already explicitly selected a brand
    //   context (af_brand_context), that selection is authoritative — we find
    //   the matching executive and use it. This prevents alphabetical ordering
    //   from silently overriding an explicit goBrand(EvoSys) selection with
    //   BookaBoost merely because B < E.
    //
    //   When a single executive exists, the button is unambiguous.
    //
    //   When multiple executives exist and no brand context is set, we MUST
    //   show labeled options — never silently default to executives[0].
    if (workspaces.length === 0 && hasExecutive) {
      const currentBrand = getBrandContext()
      // Find the executive that matches the already-selected brand context.
      const matched = currentBrand
        ? executives.find(function (e) { return e.platform_id === currentBrand.platformId })
        : null

      // Single unambiguous case: exactly one executive available.
      if (executives.length === 1) {
        const ex = executives[0]
        return (
          <button type="button" className="ctx-switch-btn"
                  onClick={function () { enterExecutive(ex.platform_id, ex.platform_name) }}
                  title={ex.platform_name + ' Executive Suite'}>
            Executive Suite
          </button>
        )
      }

      // Matched case: current brand context identifies exactly which executive.
      if (matched) {
        return (
          <button type="button" className="ctx-switch-btn"
                  onClick={function () { enterExecutive(matched.platform_id, matched.platform_name) }}
                  title={matched.platform_name + ' Executive Suite'}>
            Executive Suite
          </button>
        )
      }

      // Multiple executives, no brand context match — show labeled picker so
      // the user makes an explicit, informed choice.  Never silently pick.
      return (
        <div className="ctx-switch-wrap" ref={boxRef}>
          <button type="button" className="ctx-switch-btn"
                  aria-haspopup="menu" aria-expanded={open}
                  onClick={function () { setOpen(!open) }}>
            Executive Suite <span className="ctx-switch-caret">▾</span>
          </button>
          {open && (
            <div className="ctx-switch-menu" role="menu">
              <div className="ctx-switch-menu-head">Choose Brand</div>
              {executives.map(function (ex) {
                return (
                  <button key={ex.platform_id} type="button" role="menuitem"
                          className="ctx-switch-item"
                          onClick={function () { enterExecutive(ex.platform_id, ex.platform_name) }}>
                    <span className="ctx-switch-item-name">{ex.platform_name} — Executive Suite</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
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
                            onClick={function () { enterExecutive(ex.platform_id, ex.platform_name) }}>
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
