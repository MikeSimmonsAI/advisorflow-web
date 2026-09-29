// ONE DELETE BEHAVIOUR FOR EVERY LEADS SCREEN.
//
// Every screen that lists leads deletes them the same way: DELETE /leads/{id}
// per lead (the server removes the lead's own history, keeps independent
// records, keeps a DNC number suppressed, and refuses - with a reason - a lead
// that is the seller on an active wholesale deal). A partial failure is never
// silent: the summary says how many and the first reason the server gave.

export async function deleteLeadIds(api, ids) {
  const list = Array.from(ids || [])
  const results = await Promise.allSettled(list.map(id => api.delete(`/leads/${id}`).then(() => id)))
  const deleted = []
  const failed = []
  results.forEach((r, i) => {
    if (r.status === 'fulfilled') deleted.push(r.value)
    else failed.push({ id: list[i], reason: (r.reason && r.reason.message) || 'unknown error' })
  })
  return { deleted, failed, requested: list.length }
}

export function deleteSummary({ deleted, failed, requested }, noun = 'lead') {
  const plural = requested === 1 ? noun : `${noun}s`
  if (!failed.length) return { ok: true, text: `Deleted ${deleted.length} ${deleted.length === 1 ? noun : `${noun}s`}.` }
  if (!deleted.length && requested === 1) return { ok: false, text: `Could not delete this ${noun}: ${failed[0].reason}` }
  return {
    ok: false,
    text: `Deleted ${deleted.length} of ${requested} ${plural}. ${failed.length} could not be deleted: ${failed[0].reason}`,
  }
}

export function confirmLeadDelete(count, name) {
  const what = count === 1 ? (name ? `"${name}"` : 'this lead') : `${count} leads`
  return window.confirm(`Permanently delete ${what}? This cannot be undone.\n\n`
    + 'The lead\'s own messages, calls and notes go with it; its contact record is kept, '
    + 'and a do-not-contact number stays suppressed.')
}
