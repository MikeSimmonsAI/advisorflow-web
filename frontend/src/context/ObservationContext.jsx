/**
 * ObservationContext — Executive Observation Mode signal.
 *
 * Wraps the customer-facing components rendered inside ExecObserveShell.
 * Any component that needs to suppress a mutation control reads this context
 * and hides/disables the element when observationMode is true.
 *
 * Security note: hiding buttons is UX, not security. The backend enforces
 * require_not_observation on every mutation endpoint regardless of what
 * the frontend renders. This context is purely for presentation.
 */
import { createContext, useContext } from 'react'

const ObservationContext = createContext({ observationMode: false })

/**
 * useObservationMode()
 *
 * Returns true when the current render tree is inside Executive Observation
 * Mode. Use it to conditionally suppress mutation controls:
 *
 *   const observationMode = useObservationMode()
 *   if (observationMode) return null
 */
export function useObservationMode() {
  return useContext(ObservationContext).observationMode
}

export function ObservationProvider({ children }) {
  return (
    <ObservationContext.Provider value={{ observationMode: true }}>
      {children}
    </ObservationContext.Provider>
  )
}

export default ObservationContext
