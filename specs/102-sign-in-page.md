# Sign-in Page

> **Status:** 🚧 Implemented, in review (2026-10-16; on `feat/94-public-pages`, PR #353)
> **Created:** 2026-10-16
> **Updated:** 2026-10-16
> **Priority:** High. The public pages (spec #94) put "Sign in" in front of every visitor, and today it means four different things.
> **Author:** Product & Engineering
> **Related:** [#94 public-pages](94-public-pages.md) (the buttons this page serves), [#80 share-to-thestill](80-share-to-thestill.md) (`next` through login)

---

## Executive summary

The public site starts Google sign-in from six places. Five of them (header, footer, landing hero, the sign-in cards and the show page's Follow button) send the visitor straight to Google. They are labelled differently: "Sign in", "Continue with Google", "Follow with Google", "Sign in to follow". The sixth (a signed-in path such as `/inbox`) goes to `/login`, a page styled like the signed-in app rather than the public site.

This spec makes `/login` the one place sign-in happens. Every button on the public site links to `/login?next=<this page>`, optionally with an `intent`. The page lists the sign-in providers (Google today; Microsoft, Apple and email later), carries the Terms/Privacy consent line once, and says why the visitor is signing in. A Follow click survives sign-in: the show is followed when the visitor lands back on its page.

## Goals

1. Buttons keep the names signed-in users know: "Follow", "Transcript", "Read the transcript". Signed out, the same button leads to `/login` and back. Only buttons whose action is signing in say "Sign in". No button names a provider or says "Sign in to …".
2. One page decides how to sign in. Adding a provider changes `/login` only.
3. `/login` looks like the public site (Thestill Editorial), in the public app and the signed-in app alike (logout lands there too).
4. A visitor who clicks Follow, signs in and comes back is following the show, with no second click.
5. A URL alone never makes a signed-in user follow something.

## Non-goals

- New providers (Microsoft, Apple, email). The page is built as a list so they slot in; the providers themselves need their own OAuth or email work.
- Server changes. `next` is already validated on both sides (`safe_next_path`, `safeNextPath`).
- Intents beyond follow. Other intents are copy only.

## Design

### `/login`

`pages/Login.tsx`, styled with `public.css` inside `.public-theme`:

- Wordmark linking home.
- `h1` "Sign in to thestill" and one line saying why, from `intent`:

  | `intent` | Line |
  |----------|------|
  | `follow` | "Sign in and we'll follow the show for you. New episodes arrive summarised in your inbox." |
  | `transcript` | "Sign in to read the full transcript, with speaker names and timestamps." |
  | none | "Follow shows, read every transcript, and get a morning briefing across them." |

- Provider list: one full-width button per provider. Today "Continue with Google".
- Consent line: "By continuing you agree to the Terms of Service and Privacy Policy", both linked (`/terms`, `/privacy` work signed in or out since spec #94).
- "Not now" link back to `next` (or home).
- Already signed in, or single-user mode: redirect to `next` (unchanged).

Unknown `intent` values fall back to the default line. `next` is validated with `safeNextPath`; an invalid one is dropped.

### Buttons on the public site

`useSignIn(intent?, next?)` (helpers) returns the `/login` URL for an action (default `next`: the current page). Buttons become links (`Button` gains `to`), so they open in a new tab and show their target.

Rule (decided 2026-10-16): an action keeps its usual label and icon; signed out, it redirects to sign-in with the matching `intent` and comes back to where the action would have taken a signed-in user.

| Where | Label | Link |
|-------|-------|------|
| Header, footer | Sign in | `/login?next=<page>` |
| Landing hero | Sign in | `/login?next=/` |
| Episode Transcript tab (no lock icon) and the "Read the transcript" cards | Transcript / Read the transcript | `/login?next=<episode>?view=transcript&intent=transcript` (the signed-in reader opens on the transcript) |
| Show page Follow (hero + card) | Follow | `/login?next=/podcasts/<slug>&intent=follow` |
| Person and entity page card | See every mention | `/login?next=<page>` (signed in, the entity page lists every mention) |

### Follow survives sign-in

1. On `/login`, when `intent=follow` and `next` is a show path (`/podcasts/<slug>`), pressing a provider button stores `{slug, at}` in `sessionStorage` (`thestill.pendingFollow`) before redirecting to the provider.
2. The provider returns to `next`; the visitor is now signed in and sees the signed-in show page (`PodcastDetail`).
3. `PodcastDetail` reads the pending follow once: if the slug matches this page and it is under 15 minutes old, it clears the key and, if the show is not already followed, follows it and shows "Following <show>".

The pending follow lives in `sessionStorage`, set by a click on our own page, so a crafted link cannot make a signed-in user follow anything (goal 5). The key is cleared on first read whether or not it matched; storage failures (private mode) degrade to the old behaviour (the visitor presses Follow again).

## Testing

- Vitest: `/login` copy per intent, consent links, the provider button calls `login(next)`, the pending follow is stored only for `intent=follow` with a show `next`; `consumePendingFollow` matches slug and age and clears the key.
- Playwright (`public-pages.spec.ts`): the header Sign in opens `/login?next=…` and the page offers Continue with Google; Follow opens `/login?next=/podcasts/…&intent=follow` with the follow line.

## Rollback

Frontend only: revert the commit. No data, no migrations.
