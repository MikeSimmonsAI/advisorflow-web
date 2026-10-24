/** Shared bits for the public demo (kept apart to avoid circular imports). */
import { createContext, useContext } from 'react'

export const BOOK_DEMO = '/roofing#demo'
export const PreviewCtx = createContext(() => {})
/** preview(title, body): show what an action WOULD do in a live company. */
export const usePreview = () => useContext(PreviewCtx)
