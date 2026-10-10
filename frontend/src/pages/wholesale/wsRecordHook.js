/* React glue for the pure record state in wsListState: one GET whose newest
 * request wins, whose last good data survives a failed refresh, and which starts
 * over (no data from the previous scope) when `key` changes. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { errText } from './wsShared'
import { initialRecord, recordStarted, recordSucceeded, recordFailed, recordView, supportCode } from './wsListState'

export function useRecord(fetcher, key = '', enabled = true) {
  const [rec, setRec] = useState(initialRecord)
  const gen = useRef(0)
  const alive = useRef(true)
  const fetchRef = useRef(fetcher)
  fetchRef.current = fetcher
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const run = useCallback(async (fresh) => {
    const g = ++gen.current
    setRec((prev) => ({ ...recordStarted(fresh ? initialRecord() : prev), latest: g }))
    try {
      const data = await fetchRef.current()
      if (alive.current) setRec((prev) => recordSucceeded(prev, g, data))
    } catch (e) {
      if (alive.current) setRec((prev) => recordFailed(prev, g, errText(e), supportCode(e)))
    }
  }, [])

  useEffect(() => { if (enabled) run(true) }, [key, enabled, run])
  const reload = useCallback(() => run(false), [run])
  return { rec, view: recordView(rec), reload }
}
