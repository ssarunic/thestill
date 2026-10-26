// Spec #94 — the signed-out shell: sticky header, footer with the model
// disclaimer, the editorial token scope, and scroll restoration on Back.
import { Link, NavLink, Outlet } from 'react-router-dom'
import '../../styles/public.css'
import { useScrollRestoration } from '../../hooks/useScrollRestoration'
import { Button } from './ui'
import { useSignIn } from './helpers'

function SiteHeader() {
  const signIn = useSignIn()
  return (
    <header className="ts-header">
      <div className="ts-header-inner">
        <Link to="/" className="ts-wordmark">
          thestill
        </Link>
        <nav className="ts-header-nav" aria-label="Main">
          <NavLink to="/top" className="ts-header-link">
            Top podcasts
          </NavLink>
        </nav>
        <Button to={signIn.to} state={signIn.state} rel="nofollow">
          Sign in
        </Button>
      </div>
    </header>
  )
}

function SiteFooter() {
  return (
    <footer className="ts-footer">
      <div className="ts-footer-inner">
        <div className="ts-footer-top">
          <div className="ts-footer-brand">
            <Link to="/" className="ts-wordmark">
              thestill
            </Link>
            <span className="ts-footer-line">Read your podcasts.</span>
          </div>
        </div>
        <p className="ts-footer-legal">
          <a href="https://github.com/ssarunic/thestill" target="_blank" rel="noopener noreferrer">
            Open source on GitHub
          </a>{' '}
          · <Link to="/terms">Terms</Link> · <Link to="/privacy">Privacy</Link> · Summaries and quotes are produced by a
          language model and speech recognition; they can be wrong.
        </p>
      </div>
    </footer>
  )
}

export default function PublicLayout() {
  useScrollRestoration()
  return (
    <div className="public-theme">
      <SiteHeader />
      <main className="ts-main" id="main">
        <Outlet />
      </main>
      <SiteFooter />
    </div>
  )
}
