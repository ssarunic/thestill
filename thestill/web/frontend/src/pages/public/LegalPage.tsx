// Spec #94 — Terms and Privacy outside the public site: signed-in users and
// installs without public pages still need them (the Login page links here).
import { Link } from 'react-router-dom'
import '../../styles/public.css'
import Legal from './Legal'

export default function LegalPage({ doc }: { doc: 'terms' | 'privacy' }) {
  return (
    <div className="public-theme" style={{ minHeight: '100vh' }}>
      <div className="ts-container" style={{ paddingTop: 24 }}>
        <Link to="/" className="ts-wordmark">
          thestill
        </Link>
      </div>
      <Legal doc={doc} />
    </div>
  )
}
