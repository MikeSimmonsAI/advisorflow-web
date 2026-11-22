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
import TeamAlerts from './TeamAlerts'
import { Avatar, ErrorNote, Icon, Loading, ScreenTitle, useTeam, useTeamApi } from './TeamApp'

function roleLine(user, a) {
  if (a.is_platform) return 'Platform'
  return a.platform_name || ''
}

function ActionTile({ icon, label, tone, onClick, sub }) {
  return (
    <button type="button" className={'ta-action ta-action--' + tone} onClick={onClick}>
      <span className="ta-action-icon" aria-hidden><Icon name={icon} size={30} /></span>
      <span className="ta-action-label">{label}</span>
      {sub && <span className="ta-action-sub">{sub}</span>}
    </button>
  )
}

export function AccountScreen() {
  const navigate = useNavigate()
  const { me, signOut } = useTeam()
  const [installMsg, setInstallMsg] = useState(null)
  const user = (me && me.user) || {}
  const a = (me && me.access) || {}
  const installed = isInstalled()
  async function install() {
    if (installed) { setInstallMsg('EvoSys is already installed on this device.'); return }
    if (canPromptInstall()) { const ok = await promptInstall(); setInstallMsg(ok ? 'Installed.' : null); return }
    setInstallMsg(isIOS()
      ? 'In Safari, tap the Share button, then “Add to Home Screen”.'
      : 'In Chrome, open the ⋮ menu, then “Install app” or “Add to Home screen”.')
  }
  const line = roleLine(user, a)
  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.account} />
      <section className="ta-card ta-profile">
        <Avatar text={user.initials} size={64} />
        <div className="ta-profile-text"><div className="ta-profile-name">{user.name}</div>
          {line && <div className="ta-row-sub">{line}</div>}</div>
      </section>
      <div className="ta-actions">
        <ActionTile icon="user" label="Profile" tone="blue" onClick={() => navigate('/app/account/profile')} />
        {a.manage_access && <ActionTile icon="team" label="Team access" tone="green" onClick={() => navigate('/app/account/team-access')} />}
        <ActionTile icon="download" label="Install EvoSys" tone="blue" onClick={install} sub={installed ? 'Installed' : null} />
        <ActionTile icon="logout" label="Sign out" tone="red" onClick={signOut} />
      </div>
      {installMsg && <p className="ta-note" role="status">{installMsg}</p>}
      <TeamAlerts />
    </div>
  )
}

export function ProfileScreen() {
  const navigate = useNavigate()
  const { me, tz } = useTeam()
  const user = (me && me.user) || {}
  const a = (me && me.access) || {}
  const rights = [a.chat && 'Chat', a.announce && 'Post announcements', a.chat_manage && 'Manage chat rooms'].filter(Boolean)
  return (
    <div className="ta-screen">
      <div className="ta-subhead"><button type="button" className="ta-icon-btn" onClick={() => navigate('/app/account')} aria-label="Back"><Icon name="back" /></button><h1 className="ta-title">Profile</h1></div>
      <section className="ta-card ta-profile">
        <Avatar text={user.initials} size={56} />
        <div className="ta-profile-text"><div className="ta-profile-name">{user.name}</div><div className="ta-row-sub">{user.email}</div>
          {a.platform_name && <div className="ta-row-sub">{a.platform_name}{a.is_platform ? ' · Platform' : ''}</div>}</div>
      </section>
      <section className="ta-card">
        <h2 className="ta-h3">Your access</h2>
        <p className="ta-muted">{['Programs', 'Activity', ...rights].join(' · ')}</p>
        <p className="ta-muted ta-small">Times shown in {tz.replace('_', ' ')}.</p>
      </section>
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
