// Spec #102 — the one sign-in page. Every "Sign in" on the public site links
// here with ``next`` (where to come back to) and an optional ``intent`` (why);
// this page offers the providers and carries the consent line once. Styled
// like the public site, and also where the signed-in app sends logged-out users.
import { useEffect, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { GoogleG } from '../components/public/ui'
import { useAuth } from '../contexts/AuthContext'
import { safeNextPath } from '../utils/safeNext'
import { notNowPath, parseIntent, rememberPendingFollow, showSlugFromNext, type SignInIntent } from '../utils/signIn'
import '../styles/public.css'

const WHY: Record<SignInIntent | 'default', string> = {
  follow: "Sign in and we'll follow the show for you. New episodes arrive summarised in your inbox.",
  transcript: 'Sign in to read the full transcript, with speaker names and timestamps.',
  summary: 'Sign in to read the full summary, with the timeline, best quotes and more.',
  default: 'Follow shows, read every transcript, and get a morning briefing across them.',
}

interface Provider {
  id: string
  label: string
  icon: ReactNode
}

// One row per sign-in method; Microsoft, Apple and email slot in here.
const PROVIDERS: Provider[] = [{ id: 'google', label: 'Continue with Google', icon: <GoogleG /> }]

// Spec #104 — why the server sent the reader back here instead of signing
// them in. ``account_disabled`` also shows for a disabled session that
// landed here without the query parameter.
const REFUSALS = {
  account_disabled: 'This account has been disabled.',
  signups_closed: 'Signups are paused right now.',
} as const

type Refusal = keyof typeof REFUSALS

function parseRefusal(raw: string | null, accountDisabled: boolean): Refusal | null {
  if (raw === 'account_disabled' || raw === 'signups_closed') return raw
  return accountDisabled ? 'account_disabled' : null
}

export default function Login() {
  const { accountDisabled, adminContactEmail, isAnonymous, isAuthenticated, isLoading, isMultiUser, login } =
    useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  // Spec #94 — where to return after sign-in (validated client- and server-side).
  const next = safeNextPath(params.get('next'))
  const intent = parseIntent(params.get('intent'))
  const refusal = parseRefusal(params.get('error'), accountDisabled)

  // Already signed in, or single-user mode: nothing to do here.
  useEffect(() => {
    if (!isLoading && (isAuthenticated || !isMultiUser)) {
      navigate(next ?? '/', { replace: true })
    }
  }, [isAuthenticated, isLoading, isMultiUser, navigate, next])

  if (isLoading || !isMultiUser) return null

  const start = (provider: Provider) => {
    const show = intent === 'follow' ? showSlugFromNext(next) : null
    if (show) rememberPendingFollow(show)
    if (provider.id === 'google') login(next ?? undefined)
  }

  return (
    <div className="public-theme ts-login-page">
      <div className="ts-login">
        <Link to="/" className="ts-wordmark">
          thestill
        </Link>
        <h1 className="ts-login-title">Sign in to thestill</h1>
        {refusal ? (
          <p className="ts-login-refusal" role="alert">
            {REFUSALS[refusal]}
            {adminContactEmail && (
              <>
                {' '}
                Contact{' '}
                <a href={`mailto:${adminContactEmail}`} className="ts-link">
                  {adminContactEmail}
                </a>
                .
              </>
            )}
          </p>
        ) : (
          <p className="ts-login-why">{WHY[intent ?? 'default']}</p>
        )}
        <div className="ts-login-providers">
          {PROVIDERS.map((provider) => (
            <button key={provider.id} type="button" className="ts-btn ts-btn-secondary ts-login-provider" onClick={() => start(provider)}>
              {provider.icon}
              <span>{provider.label}</span>
            </button>
          ))}
        </div>
        <p className="ts-login-consent">
          By continuing you agree to the{' '}
          <Link to="/terms" className="ts-link">
            Terms of Service
          </Link>{' '}
          and{' '}
          <Link to="/privacy" className="ts-link">
            Privacy Policy
          </Link>
          .
        </p>
        {/* With public pages off there is nothing to go back to. */}
        {isAnonymous && (
          <Link to={notNowPath(next)} className="ts-login-back">
            Not now
          </Link>
        )}
      </div>
    </div>
  )
}
