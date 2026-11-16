/**
 * ACCOUNT — profile, install help, sign out, and (for the platform only) Team
 * access: who has Chat, who may post announcements, and who manages chat
 * rooms. The server refuses every change from anyone who is not the platform.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { isInstalled, isIOS, canPromptInstall, promptInstall } from '../pages/wholesale/appts/fieldApp'
import { LABELS, TEAM_RIGHTS } from './teamHelpers'
import { Avatar, ErrorNote, Icon, Loading, ScreenTitle, useTeam, useTeamApi } from './TeamApp'

export function AccountScreen() {
  const navigate = useNavigate()
  const { me, signOut, tz } = useTeam()
  const [installMsg, setInstallMsg] = useState(null)
  const user = (me && me.user) || {}
  const a = (me && me.access) || {}
  const installed = isInstalled()
  async function install() {
    if (canPromptInstall()) { const ok = await promptInstall(); setInstallMsg(ok ? 'Installed.' : null); return }
    setInstallMsg(isIOS()
      ? 'In Safari, tap the Share button, then “Add to Home Screen”.'
      : 'In Chrome, open the ⋮ menu, then “Install app” or “Add to Home screen”.')
  }
  const rights = [a.chat && 'Chat', a.announce && 'Post announcements',
                  a.chat_manage && 'Manage chat rooms'].filter(Boolean)
  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.account} />
      <section className="ta-card ta-profile">
        <Avatar text={user.initials} size={56} />
        <div><div className="ta-row-name">{user.name}</div><div className="ta-row-sub">{user.email}</div>
          {a.platform_name && <div className="ta-row-sub">{a.platform_name}{a.is_platform ? ' · Platform' : ''}</div>}</div>
      </section>
      <section className="ta-card">
        <h2 className="ta-h3">Your access</h2>
        <p className="ta-muted">{['Programs', 'Activity', ...rights].join(' · ')}</p>
        <p className="ta-muted ta-small">Times shown in {tz.replace('_', ' ')}.</p>
      </section>
      {(a.manage_access) && (
        <ul className="ta-list"><li><button type="button" className="ta-row" onClick={() => navigate('/app/account/team-access')}>
          <span className="ta-row-icon"><Icon name="team" /></span><span className="ta-row-main"><span className="ta-row-name">Team access</span>
            <span className="ta-row-sub">Chat, announcements and chat rooms</span></span><Icon name="chev" size={20} /></button></li></ul>
      )}
      {!installed && (
        <section className="ta-card">
          <h2 className="ta-h3">Add EvoSys to your home screen</h2>
          <button type="button" className="ta-btn ta-btn--ghost" onClick={install}>Install app</button>
          {installMsg && <p className="ta-muted">{installMsg}</p>}
        </section>
      )}
      <button type="button" className="ta-btn ta-btn--danger ta-btn--block" onClick={signOut}>Sign out</button>
    </div>
  )
}

export function TeamAccessScreen() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useTeamApi('/team-app/admin/people')
  const [openId, setOpenId] = useState(null)
  const [saving, setSaving] = useState(null)
  const [err, setErr] = useState(null)
  async function setGrants(p, keys) {
    setSaving(p.id); setErr(null)
    try { await api.put('/team-app/admin/people/' + p.id + '/grants', { keys }); reload() } catch (e) { setErr(e.message) } finally { setSaving(null) }
  }
  return (
    <div className="ta-screen">
      <div className="ta-subhead"><button type="button" className="ta-icon-btn" onClick={() => navigate('/app/account')} aria-label="Back"><Icon name="back" /></button><h1 className="ta-title">Team access</h1></div>
      <p className="ta-muted">Sales team members have Chat automatically. Turn on anything else here, one right at a time.</p>
      {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
      {loading && !data && <Loading />}
      {error && <ErrorNote error={error} onRetry={reload} />}
      {data && (
        <ul className="ta-list">
          {data.people.map(p => {
            const isOpen = openId === p.id
            const summary = p.is_platform ? 'Platform · every right'
              : [p.sales_member && 'Sales team', ...p.grants.map(k => (TEAM_RIGHTS.find(r => r.key === k) || {}).label)].filter(Boolean).join(' · ') || 'Programs only'
            return (
              <li key={p.id} className="ta-access">
                <button type="button" className="ta-row" onClick={() => setOpenId(isOpen ? null : p.id)} aria-expanded={isOpen}>
                  <Avatar text={p.initials} size={36} />
                  <span className="ta-row-main"><span className="ta-row-name">{p.name}</span><span className="ta-row-sub">{summary}</span></span>
                  <Icon name="chev" size={18} />
                </button>
                {isOpen && !p.is_platform && (
                  <div className="ta-access-body">
                    {TEAM_RIGHTS.map(r => {
                      const on = p.grants.includes(r.key)
                      return (
                        <label key={r.key} className="ta-toggle">
                          <input type="checkbox" checked={on} disabled={saving === p.id}
                                 onChange={() => setGrants(p, on ? p.grants.filter(k => k !== r.key) : [...p.grants, r.key])} />
                          <span><strong>{r.label}</strong><small>{r.help}{r.key === 'team_chat' && p.sales_member ? ' (already on as a sales team member)' : ''}</small></span>
                        </label>
                      )
                    })}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
