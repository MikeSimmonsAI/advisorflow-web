import { useEffect, useState } from 'react'
import { programCards, isBookaBoostBrand } from './programAccessModel'
import { salesToolCards } from './programDestinations'
import BookaBoostProgramAccess from './BookaBoostProgramAccess'
import CommandCenter from './v2/CommandCenter'
import { api, getCurrentUser, logout } from '../../api/client'

/**
 * Multi-program identity = one authorized chooser, not a customer CRM.
 * Caller supplies the server's /auth/my-contexts payload; this surface
 * never computes permissions or changes credentials. Deal Builder links use
 * the server's separate access response.
 *
 * UI-REDESIGN lane: Program Access V2 (Premium Command Center) - see
 * docs/redesign/program-access-v2/README.md. Earlier spec: issue #275.
 */

/**
 * Which launcher look to wear is the SERVER's answer (/auth/my-contexts
 * `brand`). A BookaBoost login gets the BookaBoost launcher; everyone else
 * gets the EvoSys command center below. Same cards either way.
 */
export default function ProgramAccessPortal(props) {
  if (isBookaBoostBrand(props.contexts)) return <BookaBoostProgramAccess {...props} />
  return <EvoSysProgramAccess {...props} />
}

function EvoSysProgramAccess({ contexts, onChoose, user: userProp, onSignOut }) {
  const { visible: workspaceCards, others, previews } = programCards(contexts)
  const user = userProp !== undefined ? userProp : getCurrentUser()
  // Deal Builder cards come from the server's /auth/my-contexts answer
  // (`deal_builder`), the same checks the /deal-builder routes enforce. An
  // older backend without that field is asked directly instead.
  const ctxDeal = contexts && contexts.deal_builder
  const [fetchedDeal, setFetchedDeal] = useState(null)
  useEffect(() => {
    if (ctxDeal) return undefined
    let active = true
    setFetchedDeal(null)
    api.get('/deal-builder/access', { noOrgContext: true, skipRedirect: true })
      .then(access => { if (active) setFetchedDeal(access) })
      .catch(() => { if (active) setFetchedDeal(null) })
    return () => { active = false }
  }, [user?.id, ctxDeal])
  const dealAccess = ctxDeal || fetchedDeal

  // PINS: this account's own preference, kept on the server. A pin only shows
  // while the server still lists that card for this login (a removed grant
  // removes the card). Pins never load = nothing pinned; cards still work.
  // The first pinned card is the FEATURED workspace.
  const [pins, setPins] = useState([])
  const [pinNote, setPinNote] = useState('')
  useEffect(() => {
    let active = true
    api.get('/launcher/pins', { noOrgContext: true, skipRedirect: true })
      .then(r => { if (active) setPins(Array.isArray(r?.keys) ? r.keys : []) })
      .catch(() => { if (active) setPins([]) })
    return () => { active = false }
  }, [user?.id])

  // NEEDS ATTENTION (the notifications list): up to three real items from the
  // server. Any failure hides the bell and nothing else.
  const [attention, setAttention] = useState([])
  useEffect(() => {
    let active = true
    api.get('/launcher/attention', { noOrgContext: true, skipRedirect: true })
      .then(r => { if (active) setAttention(Array.isArray(r?.items) ? r.items.slice(0, 3) : []) })
      .catch(() => { if (active) setAttention([]) })
    return () => { active = false }
  }, [user?.id])

  // PLATFORM HEALTH: the public readiness report (/health/ready - app,
  // database, jobs). No answer = no tile; never a made-up "LIVE".
  const [health, setHealth] = useState(null)
  useEffect(() => {
    let active = true
    api.get('/health/ready', { noOrgContext: true, skipRedirect: true })
      .then(r => { if (active) setHealth(r || null) })
      .catch(() => { if (active) setHealth(null) })
    return () => { active = false }
  }, [])

  const visible = [...workspaceCards]
  // Deal Builder / My Team / Sales Onboarding: shared with the mobile app.
  visible.push(...salesToolCards(contexts, dealAccess))

  const keyOf = cardKey
  const togglePin = (item) => {
    const k = keyOf(item)
    const before = pins
    const next = pins.includes(k) ? pins.filter(x => x !== k) : [...pins, k]
    // Only keys of cards this login can see right now are saved.
    const allowed = new Set([...visible, ...others].map(keyOf))
    const save = next.filter(x => allowed.has(x))
    setPins(save)
    setPinNote('')
    api.put('/launcher/pins', { keys: save })
      .then(r => { if (Array.isArray(r?.keys)) setPins(r.keys) })
      .catch(() => { setPins(before); setPinNote('Could not save your pins. Try again.') })
  }

  const signOut = onSignOut || (async () => { await logout(); window.location.href = '/login' })
  // Deal Builder cards are plain in-app links; every other card runs the
  // shared openProgramItem (context choice, then the destination re-checks).
  const hrefOf = item => item.type === 'deal_builder' ? item.href : undefined
  // The God Mode entry is shown only when the SERVER lists the platform
  // console for this login (owner only); /god enforces it again.
  const isOwner = user?.role === 'god_admin'
    && (contexts?.platform_contexts || []).some(c => c && c.path === '/god')
  const canManageTeam = !!contexts?.sales_team?.my_team
  // MY DAY is the sales home (/sales). Offered only when the server gives this
  // login a sales Back Office there; /sales re-checks (SalesRoute).
  const myDayHref = contexts?.has_back_office
    && (contexts.back_office_path === '/sales'
        || (contexts.back_office_destinations || []).some(d => d && d.path === '/sales')) ? '/sales' : null

  return (
    <CommandCenter user={user} cards={visible} others={others} previews={previews}
                   pins={pins} pinNote={pinNote} onTogglePin={togglePin} keyOf={keyOf}
                   onChoose={onChoose} hrefOf={hrefOf} attention={attention} health={health}
                   isOwner={isOwner} canManageTeam={canManageTeam} myDayHref={myDayHref}
                   onSignOut={signOut} />
  )
}

/** Stable per-card key for pins: the same card always has the same key. */
export function cardKey(item) {
  return (item.type || 'workspace') + ':' + (item.organization_id || '') + ':' + (item.key || '')
}

