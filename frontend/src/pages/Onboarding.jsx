/**
 * /onboarding - formerly the self-service signup form.
 *
 * The backend route it posted to (POST /onboarding/register) is retired and
 * answers 410: it created organizations with no brand, outside every scoped
 * query. The form stayed reachable by URL, so a visitor could fill in all
 * three steps, choose a password, and only then be told signup does not
 * exist. This page now says so first and points to the two real ways in:
 * an invitation link from an operator, or signing in.
 */
import { Link } from 'react-router-dom'
import '../styles/shared.css'
import './Onboarding.css'

export default function Onboarding() {
  return (
    <div className="onboarding-page">
      <div className="onboarding-card">
        <div className="onboarding-form">
          <h1 className="onboarding-title">Accounts are set up by our team</h1>
          <p className="onboarding-subtitle">
            New workspaces are created for you, with your industry and settings in place,
            and you receive an email with a one-time link to choose your password.
          </p>
          <p className="onboarding-subtitle">
            Already received that email? Open the link in it. Already have an account? Sign in.
          </p>
          <Link className="btn btn--primary onboarding-next-btn" to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  )
}
