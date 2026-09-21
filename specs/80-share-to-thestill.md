# Share to Thestill — Mobile Share Target

> **Status:** 📝 Draft
> **Created:** 2026-09-21
> **Updated:** 2026-09-21
> **Author:** Product & Engineering
> **Priority:** Medium (turns every podcast app on the phone into a Thestill input; no account linking, no new backend surface)
> **Related:** [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md) (the import endpoint this rides on), [#79 spotify-link-import](79-spotify-link-import.md) (Spotify/Apple/YouTube resolution), [#27 add-podcast-search-discoverability](27-add-podcast-search-discoverability.md) (show links → follow), [#06 authentication](06-authentication.md) (login round trip), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP, cookies, open-redirect rules), [#74 refresh-on-open](74-refresh-on-open.md) (resolve endpoint)

---

## Executive summary

Thestill can already import an episode from a Spotify, Apple Podcasts,
YouTube or bare-audio link (`POST /api/imports`, spec #31/#79) and follow
a show from a show link (`POST /api/podcasts/resolve`, spec #74). Getting a
link *into* the app from a phone, however, is copy → switch app → open
Inbox → Import → paste → submit. This spec makes Thestill a **share
target**: once the web app is installed to the home screen, it appears in
the phone's share sheet, and "Share → Thestill" from Spotify, YouTube,
Apple Podcasts, Pocket Casts or a browser lands the episode in the inbox
in two taps.

The feature is a thin client route plus installability. It adds **no
backend endpoint and no schema**: the share page extracts the link from
what the OS hands over and calls the existing import or resolve+follow
APIs. The only backend change is carrying a `next` path through the Google
login round trip so a logged-out share completes after sign-in, plus
serving the manifest and icons from the site root.

Android and desktop Chrome get the native share-sheet experience. iOS
Safari does not implement the Web Share Target API, so iOS gets two
documented fallbacks that reuse the same route: an Apple Shortcut that
appears in the share sheet, and a one-tap **Paste link** button on the
inbox.

---

## 1. Customer outcomes

| # | Outcome | How we know it is met |
|---|---|---|
| O1 | **Two taps from any podcast app.** On Android, after a one-time "Add to home screen", the user taps Share in Spotify/YouTube/Apple Podcasts/Chrome, picks Thestill, and the episode is in their inbox with the pipeline pill running. | E2E `share-target.spec.ts` + manual Android checklist (§7.4) |
| O2 | **A shared show becomes a follow.** Sharing a show/channel link (Spotify `/show/`, Apple show page, YouTube channel) follows it rather than erroring with "this is a show link". | Vitest `SharePage` show branch + E2E |
| O3 | **Logged-out shares survive login.** In multi-user mode a share from a phone that is not signed in goes through Google login and then completes, without the user re-sharing. | pytest `test_auth_next.py` + Vitest `ProtectedRoute`/`Login` + manual |
| O4 | **Clear result, never a blank page.** The share page always shows one of: imported (episode card + Open / Inbox), followed (podcast card + Open), or the resolver's own error message with a way to try another link. Nothing is silently dropped. | Vitest `SharePage` states |
| O5 | **iOS has a working path.** An iOS user can install a documented Shortcut once and then share from the share sheet, or tap **Paste link** on the inbox after copying a link. | Vitest paste button + manual iOS checklist |
| O6 | **No new trust surface.** No third-party account is connected, no token stored, nothing new persisted. The user's data footprint after a share is identical to a paste into the Import modal. | Non-goals §5, code review |
| O7 | **Duplicates and retries are harmless.** Sharing the same link twice, or reloading the share page, never creates a second inbox row. | Vitest StrictMode test + existing server-side dedup |

---

## 2. Strategy

1. **Reuse, do not add.** The share page is a client route that calls the
   two APIs that already exist. Every URL kind the share sheet can produce
   is one the resolver chain already accepts (`ImportService` default
   lineup: YouTube, Apple, Spotify, bare audio; `RSSMediaSource` for show
   links). The error copy users see is the resolver's own, so behaviour is
   identical to the Import modal by construction.
2. **Installability is the delivery vehicle.** The share sheet only lists
   installed web apps. A web app manifest with a `share_target` entry, real
   icons and a gentle install nudge is the whole "integration" — there is
   nothing to register with Spotify, Apple or Google.
3. **GET share target, no service worker.** The GET form of `share_target`
   delivers `title`, `text` and `url` as query parameters and needs no
   service worker. Files (POST) would need one; we do not accept files.
   Keeping the app free of a service worker avoids the whole offline/cache
   invalidation problem that spec #68 and PR #239 just fought.
4. **Classify on the client, decide on the server.** A small pure helper
   sorts the shared link into `episode`, `show` or `unknown` using the same
   URL shapes `utils/url_patterns.py` recognises. `episode` and `unknown`
   go to `POST /api/imports`; `show` goes to `POST /api/podcasts/resolve`
   then `POST /api/podcasts/{slug}/follow`. Anything the client cannot
   classify is still sent, so the server's resolver, not the client, has
   the last word.
5. **Auth continuity is a `next` path, validated like an open redirect.**
   `ProtectedRoute` already bounces to `/login`; it learns to carry the
   current path, `Login` passes it to `/api/auth/google/login?next=`, the
   callback returns to it. The value is accepted only as a same-origin
   relative path.
6. **Meet each platform where it is.** Android and desktop Chrome: native.
   iOS: Shortcut + Paste button, both hitting the same `/share` route so
   there is one code path to test. No native app.

---

## 3. Design

### 3.1 Web app manifest

New file `thestill/web/frontend/public/manifest.webmanifest` (Vite copies
`public/` to the build root):

```json
{
  "name": "Thestill",
  "short_name": "Thestill",
  "description": "Podcast transcripts, summaries and briefings",
  "id": "/",
  "start_url": "/inbox",
  "scope": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#111827",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ],
  "share_target": {
    "action": "/share",
    "method": "GET",
    "params": { "title": "title", "text": "text", "url": "url" }
  }
}
```

`index.html` gains `<link rel="manifest" href="/manifest.webmanifest">`,
`<meta name="theme-color">`, `<link rel="apple-touch-icon"
href="/icons/apple-touch-icon.png">` and replaces the placeholder
`/vite.svg` favicon. Icons are a first-cut monogram in the brand colour;
a designed icon can replace the files later without touching code.

### 3.2 Serving root-level static files

[app.py:775-795](../thestill/web/app.py#L775-L795) mounts `/assets` and
then serves `index.html` for **every** other non-API path. Today that means
`/vite.svg` returns HTML; tomorrow it would mean the manifest and icons do
too, and Chrome would refuse to install. Change the catch-all to serve a
file from `static/` when the requested path names an existing **regular
file directly under the build root or under `static/icons/`**, and fall
through to the SPA shell otherwise. Implementation notes:

- Resolve with `Path.resolve()` and require the result to be inside
  `static_dir` (no `..` escapes), and require `is_file()`.
- Content types: `.webmanifest` → `application/manifest+json`; the rest via
  `mimetypes`.
- Cache: `Cache-Control: public, max-age=3600` for the manifest and icons
  (unhashed, but changing rarely); the shell keeps its `no-cache`.
- API/`webhook`/`mcp`/`docs` prefixes are checked first, unchanged.

### 3.3 CSP

[security_headers.py:52](../thestill/web/middleware/security_headers.py#L52)
sets `default-src 'self'`, which covers `manifest-src`; icons are
`img-src 'self'`. No CSP change. A test asserts the header is present and
unchanged on `/share` and `/manifest.webmanifest`.

### 3.4 Share route and page

New route `share` under the protected `Layout` in
[App.tsx](../thestill/web/frontend/src/App.tsx), rendering
`pages/Share.tsx`.

**Input.** Two pure helpers in `src/utils/shareTarget.ts`:

- `extractSharedUrl({ url, text, title })` returns the link to act on or
  `null`. Precedence: `url` param if it parses as `http(s)`; else the first
  `http(s)://` URL found in `text`, then in `title`; else a bare
  `spotify:episode:` / `spotify:show:` URI in any field (the resolver
  accepts those). Trailing `.,;:)]` and quotes are trimmed. Any other
  scheme (`javascript:`, `data:`, `file:`) yields `null`. Spotify on
  Android shares `"<Episode> https://open.spotify.com/episode/…?si=…"` in
  `text` with an empty `url`; YouTube shares the `youtu.be` link in `text`;
  Apple Podcasts shares the `podcasts.apple.com/…?i=` link in `url`.
- `classifyShareUrl(url)` returns `'episode' | 'show' | 'unknown'`:
  - Spotify: `/episode/<22 chars>` or `spotify:episode:` → episode;
    `/show/` or `spotify:show:` → show; `spotify.link/` → unknown (the
    server expands it).
  - Apple: `podcasts.apple.com` with `?i=<digits>` → episode; without →
    show.
  - YouTube: `watch?v=`, `youtu.be/`, `/shorts/`, `/live/` → episode;
    `/channel/`, `/c/`, `/user/`, `/@handle` → show; `playlist?list=` →
    unknown (the server rejects it with its own message).
  - `.mp3/.m4a/.opus/.ogg/.wav` path → episode. Everything else →
    unknown.

**Behaviour.**

| State | Trigger | UI |
|---|---|---|
| `empty` | no URL extracted | "Nothing to import" + the shared text (if any) rendered as plain text + **Paste a link** (opens `ImportEpisodeModal`) |
| `working` | URL extracted | spinner, "Importing from Spotify…" (host name only) |
| `imported` | `POST /api/imports` 200 | `EpisodeCard`-style row from `ImportPayload`, buttons **Open episode** (`/podcasts/{parent.slug}/episodes/{episode slug}` when a parent exists, else Inbox) and **Go to inbox**; "Already in your inbox" note when `deduplicated` |
| `followed` | resolve 200 then follow 201 or 409 | podcast title, **Open podcast** (`/podcasts/{slug}`); 409 renders "Already following" |
| `error` | any 4xx/5xx | the server's `detail` verbatim (React-escaped) + **Try another link** (opens `ImportEpisodeModal` pre-filled with the URL) + **Go to inbox** |

The request fires exactly once per distinct URL (a `useRef` keyed on the
URL; React StrictMode double-invokes effects in dev). On the first
transition out of `working`, the page calls `history.replaceState` to
drop the query string so a reload or Back shows the result, not a re-run.
The page is inside `ProtectedRoute`, so an unauthenticated multi-user
visit goes to login first (§3.5).

The route is ordinary SPA navigation, so the initial GET from the share
sheet never needs the session cookie (it is `SameSite=strict`; an
OS-initiated navigation may not carry it). The page's own `fetch` calls
are same-origin and do carry it. This is why the share target must be a
client route, not a server endpoint.

### 3.5 Auth continuity (`next`)

- `ProtectedRoute` redirects to `/login?next=<encodeURIComponent(pathname + search)>`.
- `Login` reads `next` and calls `login(next)`; `AuthContext.login(next?)`
  navigates to `/api/auth/google/login?next=…` only when `next` passes the
  same client-side check as the server (starts with a single `/`).
- [auth.py `google_login`](../thestill/web/routes/auth.py#L193) accepts an
  optional `next` query parameter. It is stored in an `oauth_next` cookie
  (httpOnly, `samesite=lax`, 10 min, same helper as `oauth_state`) only if
  `_safe_next_path(value)` accepts it: a string of at most 2048 characters
  that starts with `/`, does not start with `//` or `/\`, contains no
  scheme (`:` before the first `/` or `?`) and no control characters.
  Anything else is ignored and logged at debug.
- [`google_callback`](../thestill/web/routes/auth.py#L230) redirects to the
  cookie value when present and valid, else `/`, and deletes the cookie.
  Validation runs again on read; the cookie is not trusted just because
  we set it.
- Single-user mode never redirects to login, so nothing changes there.

### 3.6 Install nudge

`components/InstallNudge.tsx`, rendered at the top of the Inbox on
viewports under the `md` breakpoint when the app is **not** already
running standalone (`matchMedia('(display-mode: standalone)')`):

- Android/desktop Chrome: capture `beforeinstallprompt`; show "Add
  Thestill to your home screen to share episodes from other apps" with
  **Add** (calls `prompt()`) and **Not now**.
- iOS Safari (user agent check, no `beforeinstallprompt`): the same card
  with a link to the docs section for the Shortcut and Add to Home Screen
  steps.
- Dismissal is stored in `localStorage` (`thestill.installNudge.dismissedAt`)
  and suppresses the card for 30 days. The card never blocks content.

### 3.7 iOS fallbacks

- **Shortcut.** Documented in `docs/imports.md` with an iCloud link once
  published: *Receive URLs from Share Sheet → URL-encode Shortcut Input →
  Open URL `https://<host>/share?url=<encoded>`*. Because it opens the
  same route, it inherits O2–O4 and O7 with no extra code.
- **Paste link.** A `PasteLinkButton` in the inbox header (touch devices,
  `navigator.clipboard.readText` available). On tap it reads the clipboard
  (iOS shows its own paste prompt) and navigates to
  `/share?url=<encoded>`. If the clipboard has no usable URL or the API is
  unavailable, it opens `ImportEpisodeModal` instead.

### 3.8 Security notes

- The only new server input is `next`; it is validated as an open-redirect
  guard both when set and when read (§3.5).
- Shared `title`/`text` are rendered as text nodes only, never as HTML or
  in `href`.
- The URL that reaches the server goes through the existing SSRF guard
  (`utils/url_guard.py`) inside the resolvers; the share page adds no
  fetch of its own.
- Root static serving is allowlisted to real files under the build root,
  resolved and containment-checked (§3.2).

---

## 4. Tasks

Ordered by dependency. Each task lists its files and its done criterion.

| # | Task | Files | Done when |
|---|---|---|---|
| T1 | Manifest, icons, HTML head | `frontend/public/manifest.webmanifest`, `frontend/public/icons/{icon-192,icon-512,icon-maskable-512,apple-touch-icon}.png`, `frontend/index.html` | `npm run build` emits the files at the build root; `<link rel="manifest">` present; favicon no longer points at `vite.svg` |
| T2 | Serve root static files | `thestill/web/app.py` (`serve_spa`) | Tests §7.1 (a) pass: manifest/icons served with correct types, client routes still get the shell, traversal refused |
| T3 | Share helpers | `frontend/src/utils/shareTarget.ts` (+ `.test.ts`) | Tests §7.2 (d)(e) pass |
| T4 | Share page + route | `frontend/src/pages/Share.tsx` (+ `.test.tsx`), `frontend/src/App.tsx`, `frontend/src/api/types.ts` if an episode-slug field is missing from `ImportPayload` | Tests §7.2 (f) pass; route reachable at `/share` |
| T5 | `next` through login | `thestill/web/routes/auth.py`, `tests/unit/web/test_auth_next.py`, `frontend/src/components/ProtectedRoute.tsx`, `frontend/src/pages/Login.tsx`, `frontend/src/contexts/AuthContext.tsx` (+ tests) | Tests §7.1 (b) and §7.2 (g) pass |
| T6 | Install nudge | `frontend/src/components/InstallNudge.tsx` (+ test), `frontend/src/pages/Inbox.tsx` | Tests §7.2 (h) pass; card absent in standalone mode |
| T7 | Paste link button | `frontend/src/components/PasteLinkButton.tsx` (+ test), `frontend/src/pages/Inbox.tsx` | Tests §7.2 (i) pass |
| T8 | E2E | `frontend/tests/share-target.spec.ts`, `frontend/tests/manifest.spec.ts` | §7.3 green in `npm run test:e2e:ci` |
| T9 | Docs + index | `docs/imports.md` ("Share from your phone": Android install, iOS Shortcut, Paste link), `docs/web-server.md` (`next` on the login route, root static files), `specs/README.md` row | Reviewed; links resolve |
| T10 | Device verification | — | §7.4 checklist completed on one Android and one iOS device and recorded in this spec's status line |

Estimated effort: T1–T7 about one engineering day; T8–T10 half a day.

---

## 5. What not to implement

| Not doing | Why |
|---|---|
| **Service worker / offline / caching** | Not needed for GET share targets; would reintroduce the stale-shell class of bugs PR #239 fixed. Revisit only if push notifications or offline reading get their own spec. |
| **POST share target, file/audio sharing** | Needs a service worker and an upload path. Links cover the use case. |
| **Native iOS/Android apps or share extensions** | A different project. The Shortcut and Paste button cover iOS with zero native code. |
| **Spotify/Apple/YouTube account linking** | Out of scope here; Spotify's developer policy makes it non-viable for a hosted app anyway (see the analysis preceding this spec). |
| **A new `/api/share` endpoint or a `source=share` inbox value** | The existing import and resolve endpoints are the contract. Attribution analytics are not a goal. |
| **Importing playlists, channels or whole shows as episodes** | Show links follow; episode links import. The server's existing rejection copy handles playlist links. |
| **Queueing or retrying failed shares in the background** | A failed share shows the error and a retry affordance. No persistence of share attempts. |
| **Handling shared text with no URL** | Renders the "Nothing to import" state. We do not search the corpus or guess. |
| **Android App Links / iOS Universal Links for our own domain** | Unrelated to receiving shares; would need `assetlinks.json` and Apple association files. Separate spec if ever wanted. |
| **First-run onboarding changes** | The first-run experience (corpus-seeded inbox, interest chips) is its own spec. The install nudge is the only onboarding-adjacent UI here. |
| **Cross-origin `next` targets or full URLs** | Only same-origin relative paths are accepted; there is no allowlist of external hosts. |
| **Standalone-mode polish on iOS** (splash screens, status bar styling) | Icons and `apple-touch-icon` only. The rest is cosmetic and can follow. |

---

## 6. Open questions

- **Google OAuth from an iOS standalone web app.** iOS runs installed web
  apps in a separate context whose cookies are not shared with Safari. The
  login redirect happens inside that context, so it should work, but T10
  must confirm that a logged-out share from the home-screen app completes
  on iOS. If it does not, the iOS install advice becomes "keep using
  Safari" and only the Shortcut is recommended.
- **Episode slug in `ImportPayload`.** If the response lacks what the
  episode route needs, T4 adds the field to the existing payload (an
  additive change to `POST /api/imports`, documented in spec #02) rather
  than a second request.
- **Icon design.** Placeholder monogram in T1; a designed icon replaces
  the PNGs without a code change.

---

## 7. Definition of done — tests that must pass

The feature is done when every test below passes in CI and the manual
checklist in §7.4 is recorded. All existing gates (`make check`, `npm run
lint`, `tsc -b`, `vitest run`, `npm run test:e2e:ci`) stay green.

### 7.1 Backend (pytest)

**(a) `tests/unit/web/test_static_root_files.py`** — with a temporary
`static/` containing `index.html`, `manifest.webmanifest`, `icons/icon-192.png`:

1. `GET /manifest.webmanifest` → 200, `Content-Type: application/manifest+json`, body parses as JSON, `share_target.action == "/share"`, `share_target.method == "GET"`, `params` has `title`, `text`, `url`.
2. `GET /icons/icon-192.png` → 200, `Content-Type: image/png`, `Cache-Control` contains `max-age`.
3. `GET /share?url=x` → 200, body is the shell (`index.html`), `Cache-Control: no-cache, must-revalidate`.
4. `GET /inbox`, `GET /podcasts/some-slug` → shell (client routes unaffected).
5. `GET /icons/../index.html`, `GET /..%2fpyproject.toml`, `GET /icons/` → shell or 404, never a file outside `static/`, never a directory listing.
6. `GET /api/nonexistent` → 404 JSON from FastAPI, not the shell (prefix guard unchanged).
7. `GET /manifest.webmanifest` carries the `Content-Security-Policy` header with `default-src 'self'`.

**(b) `tests/unit/web/test_auth_next.py`** (multi-user harness from
`auth_harness.py`, Google client mocked):

1. `GET /api/auth/google/login?next=/share?url=https%3A%2F%2Fopen.spotify.com%2Fepisode%2Fabc` → 302 to Google; response sets `oauth_next` cookie whose value is that path; cookie is httpOnly, `samesite=lax`, `max-age=600`.
2. `GET /api/auth/google/login` without `next` → no `oauth_next` cookie.
3. Rejected `next` values set no cookie: `https://evil.example/`, `//evil.example`, `/\evil.example`, `javascript:alert(1)`, `share` (no leading slash), a 2049-character path, a path containing `\r\n`.
4. Callback with valid state and an `oauth_next` cookie of `/share?url=…` → 302 `Location` equals that path; `oauth_next` cookie deleted; auth cookie set.
5. Callback with a tampered `oauth_next` cookie (`//evil.example`) → 302 to `/`.
6. Callback with no `oauth_next` cookie → 302 to `/` (existing behaviour, regression guard).
7. Single-user mode: `GET /api/auth/google/login?next=/share` → 400 as today.

### 7.2 Frontend unit (vitest)

**(d) `utils/shareTarget.test.ts` — `extractSharedUrl`:**

1. `url` param that parses as https wins over `text`.
2. Spotify Android share: `{ url: '', text: 'The Daily · Ep https://open.spotify.com/episode/7kQ2xN9pZ1aB3cD4eF5gH6?si=abc' }` → the `open.spotify.com` URL including `?si=`.
3. YouTube share: `text: 'Watch "…" https://youtu.be/dQw4w9WgXcQ'` → the `youtu.be` URL.
4. Apple share: `url: 'https://podcasts.apple.com/us/podcast/x/id123?i=456'` → unchanged.
5. Trailing punctuation trimmed: `'… https://example.com/a.mp3).'` → `https://example.com/a.mp3`.
6. Bare URI: `text: 'spotify:episode:7kQ2xN9pZ1aB3cD4eF5gH6'` → that URI.
7. `url: 'javascript:alert(1)'`, `text: 'data:text/html,…'` → `null`.
8. No URL anywhere → `null`.
9. `title` is used only when `url` and `text` yield nothing.

**(e) `utils/shareTarget.test.ts` — `classifyShareUrl`:**

1. Spotify `/episode/`, `/intl-de/episode/`, `spotify:episode:` → `episode`; `/show/`, `spotify:show:` → `show`; `spotify.link/abc` → `unknown`.
2. Apple with `?i=` → `episode`; without → `show`.
3. YouTube `watch?v=`, `youtu.be/`, `/shorts/`, `/live/` → `episode`; `/channel/`, `/@handle`, `/c/`, `/user/` → `show`; `playlist?list=` → `unknown`.
4. `.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav` paths → `episode`.
5. `https://example.com/blog` → `unknown`.

**(f) `pages/Share.test.tsx`** (API client mocked, router at `/share?…`):

1. Episode URL → `importEpisode` called once with exactly the extracted URL, `working` state shown first, then `imported` with the episode title, **Open episode** and **Go to inbox** links.
2. Same test rendered inside `React.StrictMode` → `importEpisode` still called exactly once.
3. `deduplicated: true` → "Already in your inbox" note visible.
4. Show URL → `resolvePodcast` called with the URL, then `followPodcast` with the returned slug; `followed` state with **Open podcast** linking to `/podcasts/{slug}`.
5. Show URL where `followPodcast` rejects with a 409-style error → still `followed` with "Already following".
6. `importEpisode` rejects with `Error('Could not find "X" in the Apple Podcasts directory…')` → that text is rendered verbatim, **Try another link** opens `ImportEpisodeModal` with the URL pre-filled, **Go to inbox** present.
7. `unknown` classification (`https://example.com/blog`) → sent to `importEpisode` (server decides).
8. No URL (`/share?text=hello`) → "Nothing to import", the text `hello` rendered as text, **Paste a link** present; no API call.
9. After success, `window.location.search` is empty (query dropped via `replaceState`).
10. Shared `title` containing `<img onerror>` renders as literal text (no element created).

**(g) auth continuity:**

1. `ProtectedRoute` in multi-user mode, unauthenticated, at `/share?url=https%3A%2F%2Fa` → navigates to `/login?next=%2Fshare%3Furl%3Dhttps%253A%252F%252Fa`.
2. Single-user mode → renders children, no redirect.
3. `Login` at `/login?next=%2Fshare%3Furl%3Da` → clicking the button calls `login('/share?url=a')`.
4. `AuthContext.login('/share?url=a')` sets `window.location.href` to `/api/auth/google/login?next=%2Fshare%3Furl%3Da`; `login('https://evil')` and `login('//evil')` navigate without a `next` parameter.

**(h) `components/InstallNudge.test.tsx`:**

1. Not standalone + `beforeinstallprompt` fired → card visible; **Add** calls the captured event's `prompt()`.
2. **Not now** hides the card and writes `thestill.installNudge.dismissedAt`; re-mount within 30 days → hidden; with a timestamp older than 30 days → visible.
3. `display-mode: standalone` matches → never rendered.
4. iOS user agent, no `beforeinstallprompt` → iOS variant with the docs link.
5. `localStorage` throwing → card still renders and dismiss does not crash.

**(i) `components/PasteLinkButton.test.tsx`:**

1. `navigator.clipboard.readText` resolves to a URL → navigates to `/share?url=<encoded>`.
2. Resolves to non-URL text → opens `ImportEpisodeModal`, no navigation.
3. `readText` rejects (permission denied) → opens `ImportEpisodeModal`.
4. `navigator.clipboard` undefined → button not rendered.

### 7.3 End-to-end (Playwright, hermetic, runs in CI)

**(j) `tests/share-target.spec.ts`** at a 393 px phone viewport with
`/api/**` stubbed (auth status authenticated, multi-user):

1. `goto('/share?title=…&text=Listen%20to%20X%20https%3A%2F%2Fopen.spotify.com%2Fepisode%2F7kQ2xN9pZ1aB3cD4eF5gH6%3Fsi%3D1')` with `POST /api/imports` stubbed 200 → the stub received `{ url: 'https://open.spotify.com/episode/7kQ2xN9pZ1aB3cD4eF5gH6?si=1' }`; success card visible; **Go to inbox** lands on `/inbox`.
2. Show link with resolve and follow stubs → both called in order; **Open podcast** lands on `/podcasts/<slug>`.
3. Import stub returns 400 `{ detail: 'Spotify exclusive …' }` → the message is visible; **Try another link** opens the modal with the URL in the input.
4. Every button and link on the result screen is at least 44 × 44 px.
5. Unauthenticated stub → URL becomes `/login?next=…` carrying the share path.

**(k) `tests/manifest.spec.ts`:**

1. `page.request.get('/manifest.webmanifest')` → 200, JSON, `share_target.action === '/share'`, three icons declared.
2. The shell has `link[rel=manifest]` and `meta[name=theme-color]`.
3. Each declared icon URL returns 200 with `image/png`.

### 7.4 Manual device checklist (recorded in the status line, not CI)

- [ ] Android Chrome: install from the nudge; Thestill appears in the share sheet.
- [ ] Share an episode from Spotify → inbox row with pipeline pill; server log shows `spotify_episode_matched`.
- [ ] Share a video from YouTube and an episode from Apple Podcasts → inbox rows.
- [ ] Share a Spotify show link → podcast followed, detail page opens.
- [ ] Sign out, share from Spotify → Google login → import completes without re-sharing.
- [ ] Share the same episode twice → one inbox row, "Already in your inbox".
- [ ] iOS: install the Shortcut, share from Spotify → `/share` opens in Safari and imports.
- [ ] iOS: copy a link, tap **Paste link** → imports.
- [ ] iOS: home-screen app, logged out, share via Shortcut → login completes (§6 open question resolved either way).
- [ ] Desktop Chrome: Thestill appears as a share target after install.
- [ ] Lighthouse "installable" audit passes on the production build.
