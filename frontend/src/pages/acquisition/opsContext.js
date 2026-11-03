// Shared by the Acquisition Hub pages: the selected client's context.
import { createContext, useContext } from 'react'

export const OpsCtx = createContext(null)
export const useOps = () => useContext(OpsCtx)
export const OPS = '/acquisition-ops'
