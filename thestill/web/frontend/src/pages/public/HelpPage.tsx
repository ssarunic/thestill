// Help pages outside the public site: signed-in users and installs without
// public pages still need them (Settings links to /claude).
import { Link } from 'react-router-dom'
import '../../styles/public.css'
import Help, { type HelpDoc } from './Help'

export default function HelpPage({ doc }: { doc: HelpDoc }) {
  return (
    <div className="public-theme" style={{ minHeight: '100vh' }}>
      <div className="ts-container" style={{ paddingTop: 24 }}>
        <Link to="/" className="ts-wordmark">
          thestill
        </Link>
      </div>
      <Help doc={doc} />
    </div>
  )
}
