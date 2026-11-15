import { useParams, Navigate } from 'react-router-dom'
import { API_BASE } from '../../api/client'

/**
 * BUILD QUOTE, INSIDE THE DEAL.
 *
 * The same Salesperson Deal Builder page as /team-prices/sales-deal-builder,
 * opened for one deal (?opp=<id>). The page loads the customer, the deal and
 * the credited salesperson from the server, restores the quote saved on the
 * deal, and saves revisions back to it. Commission comes only from the
 * server's /deal-builder/commission answer for this login's seat.
 */
const frameStyle = { position: 'fixed', inset: 0, display: 'block', width: '100%', height: '100dvh', border: 0, background: '#f3f6f8' }

function token() {
  try { return localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token') } catch { return null }
}

export default function DealQuote() {
  const { oppId } = useParams()
  try { window.__EVOSYS_API_BASE__ = API_BASE } catch { /* ignore */ }
  if (!token()) return <Navigate to={'/login?next=' + encodeURIComponent('/sales/opportunities/' + oppId + '/quote')} replace />
  return <iframe title="Deal Builder for this deal" style={frameStyle}
                 src={'/team-prices/sales-builder-frame.html?opp=' + encodeURIComponent(oppId)} />
}
