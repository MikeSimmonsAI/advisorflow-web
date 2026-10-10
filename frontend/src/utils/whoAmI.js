// Who is signed in, in THIS workspace: GET /work/identity (user_id, name,
// workspace_role). The stored login object carries no user id, so "My leads",
// "Assigned to me" and "You" read it from here. One request per page load.
import { useEffect, useState } from 'react'
import { api } from '../api/client'

let pending = null

export function whoAmI() {
  if (!pending) pending = api.get('/work/identity').catch(() => { pending = null; return null })
  return pending
}

export function useWhoAmI() {
  const [me, setMe] = useState(null)
  useEffect(() => { let on = true; whoAmI().then(d => { if (on) setMe(d) }); return () => { on = false } }, [])
  return me
}
