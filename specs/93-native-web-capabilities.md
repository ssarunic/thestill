# Native Web Capabilities — Ideation

> **Status:** 💡 Proposal
> **Created:** 2026-10-01
> **Updated:** 2026-10-01
> **Author:** Product & Engineering
> **Priority:** Medium. Nothing here is broken today; this is a map of what the phone would give us for free if we asked properly.
> **Related:** [#80 share-to-thestill](80-share-to-thestill.md) (manifest, icons, share target), [#61 unified-av-playback-session](61-unified-av-playback-session.md) (Media Session, already shipped), [#51 briefing-email-delivery](51-briefing-email-delivery.md) (the `channel` column push would join), [#50 scheduled-briefings](50-scheduled-briefings.md) / [#84 scheduled-only-briefings](84-scheduled-only-briefings.md) (the "briefing ready" moment), [#90 continue-listening](90-continue-listening.md) (resume state that offline and badges build on), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP)

---

## Executive summary

Thestill is a web app, and phones now give web apps a good share of what
used to need a native app: lock-screen controls, push notifications, an
icon badge, offline storage, a slot in the share sheet, AirPlay and Cast.
We use one of these properly (the lock-screen player from spec #61) and
one in passing (`navigator.share` in the share button). Everything else is
unused, mostly because the site is not installable: `index.html` has no
web app manifest, no service worker, no `apple-touch-icon` and no
`theme-color`, and the favicon is still Vite's default `vite.svg`. On iOS,
most of what follows only works once the site has been added to the Home
Screen, so installability gates nearly everything else.

This document is ideation, not a plan. It lists each capability, what it
would do for a Thestill listener, what it costs, and what could go wrong,
and ends with a suggested order. It also sets one rule ahead of any
implementation, because it is the easiest thing to get wrong and the
hardest to undo:

> **Thestill never asks for a permission the user did not go looking for.**
> No prompt on page load, no prompt on first visit, no modal asking to
> "enable notifications", no install nag that comes back. Every permission
> request starts from a control the user tapped knowing what it does.

Section 3 turns that rule into concrete UX before any feature is
discussed.

---

## 1. Where we are today

| Capability | State | Where |
|---|---|---|
| Lock-screen / Control Centre player (Media Session) | ✅ Shipped: title, podcast, artwork, play/pause/stop, ±15 s, scrubber, position | [mediaSession.ts](../thestill/web/frontend/src/utils/mediaSession.ts), [PlayerContext.tsx:712](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L712) |
| Picture-in-picture for video | ✅ Shipped | `PlayerContext` (`pipSupported`, `pipActive`) |
| Web Share (outbound) | ✅ Shipped | [ShareButton.tsx](../thestill/web/frontend/src/components/ShareButton.tsx) |
| Web app manifest, icons, `theme-color` | ❌ None. Planned in #80 | [index.html](../thestill/web/frontend/index.html) |
| Service worker | ❌ None. #80 explicitly ships without one | — |
| Push notifications | ❌ None. Briefings go by email only (#51); `DeliveryChannel` was designed so push can be added later | [briefing_delivery.py:39](../thestill/models/briefing_delivery.py#L39) |
| Everything else in §4 | ❌ Unused | — |

The lock-screen player has one known gap: episodes playing through the
YouTube iframe engine (#62) get neither our Media Session handlers nor
reliable background playback on iOS. Only the native engine gets the full
lock-screen experience.

---

## 2. Platform reality

The two phone platforms differ a lot, and the differences decide most of
the trade-offs below.

| | iOS / iPadOS (Safari, and every iOS browser, since all use WebKit) | Android (Chrome and other Chromium browsers) |
|---|---|---|
| Install | Manual only: Share → Add to Home Screen. No API to trigger it, no install event. | `beforeinstallprompt` lets us offer install from our own button; Chrome also shows its own mini-infobar. |
| Push | iOS 16.4+, **Home Screen apps only**. Not available in a Safari tab. iOS 18.4 added Declarative Web Push (no service worker code needed to show a notification). | Any tab or installed app. |
| Badge | iOS 16.4+, Home Screen apps only, and **only after notification permission is granted**. | Installed apps; no permission needed. |
| Share target (incoming) | ❌ Not supported. #80 works around it with an Apple Shortcut. | ✅ |
| Background work | None. A service worker only runs to handle a push or a fetch. | Background Sync, Periodic Background Sync (installed apps, engagement-gated), Background Fetch. |
| Storage eviction | Browser-tab data is wiped after 7 days of no use; Home Screen apps are exempt. Large quotas since iOS 17. | Best-effort unless `persist()` is granted; large quotas. |

**What this means for us:** on iOS, installing to the Home Screen is the
gate for push, badges and reliable storage. On Android almost everything
works from a tab, and installing adds polish. Any user-facing copy has to
say "Add to Home Screen first" on iOS without nagging, which §3.4 covers.

---

## 3. Permission and prompting principles

This is the user-experience contract. It applies to every capability in
§4 that involves a permission or an install.

### 3.1 What we never do

- Call `Notification.requestPermission()` on load, after a timer, on scroll,
  or as a side effect of an unrelated tap (for example "Play").
- Show a pre-permission modal or full-width banner ("Thestill would like to
  send you notifications").
- Ask again after a "no". Dismissing an offer is permanent for that offer
  on that device.
- Show the install hint more than once per device, or show it before the
  user has used the app for something real.
- Gate a feature behind a permission when a non-permission alternative
  exists (email already exists for briefings).

Browsers already penalise the opposite behaviour. Safari rejects a
permission request that does not come from a user gesture, and Chrome
switches sites with low acceptance rates to a "quiet" permission UI (a
crossed-out bell in the address bar). Asking badly does not just annoy
people; it makes the permission permanently harder to get.

### 3.2 Where notifications are turned on

There are two entry points, in order of importance.

1. **Settings → Notifications (the main switch).** A new section on the
   Settings page, next to the existing ones, listing each notification
   type with its own toggle and a short line saying what it sends and how
   often:

   ```text
   Notifications on this device
   ─────────────────────────────
   Briefing ready            [ off ]   Once a day, when your scheduled briefing is cut
   New episodes              [ off ]   At most one summary notification a day
   Imports finished          [ off ]   When a shared or imported episode is ready to read

   Email
   ─────
   Briefing by email         [ on  ]   (existing #51 setting)
   ```

   Turning on the first toggle is the user gesture that triggers the
   browser's permission prompt. If the user declines, the toggle goes back
   to off, with a line explaining how to change it in the phone's settings
   later (§3.3).

2. **One small inline offer, in context, at most once.** The natural moment
   is the briefing card from #84, which already shows "next at 08:00". Once
   a user has opened briefings on a few different days (to be decided; 3 is
   a reasonable starting point), the card gains a quiet text link, not a
   button and not a banner:

   ```text
   Next briefing at 08:00 · Notify me when it's ready  ✕
   ```

   Tapping **Notify me** is the gesture and goes straight to the browser
   prompt. Tapping **✕** hides it forever on this device. The link never
   appears for a user who already has briefing email on, since they
   already get the briefing delivered.

That's it. There's no bell icon in the header and no badge on Settings
announcing that notifications exist.

### 3.3 After a decision

| Browser permission | What Settings shows |
|---|---|
| `default` (never asked) | Toggles off and live. The first one switched on triggers the prompt. |
| `granted` | Toggles reflect what the server has saved for this device; turning them all off unsubscribes the device. |
| `denied` | Toggles disabled, with one line: "Notifications are blocked for Thestill. You can allow them in iOS Settings → Notifications → Thestill" (or the Chrome site-settings path on Android). We never call `requestPermission` again; the browser would ignore it anyway. |
| Unsupported (iOS Safari tab, old browser) | Toggles hidden. On iOS a single line: "Add Thestill to your Home Screen to get notifications on this iPhone", with a link to how. Nothing is shown anywhere outside Settings. |

### 3.4 Installing

- **Android:** keep the `beforeinstallprompt` event and offer
  **Install app** in two places: Settings, and the account menu. No
  automatic banner from us. Chrome's own mini-infobar is outside our
  control but appears rarely.
- **iOS:** an **Add to Home Screen** row in Settings that opens a short
  illustrated how-to (Share → Add to Home Screen). The only place this
  appears outside Settings is the iOS line in §3.3, and only for someone
  already in the Notifications section.
- #80's "install nudge" should follow these rules too: once, dismissible,
  and only after the user has done something real (for example their
  first import).

### 3.5 What a notification is allowed to be

Permission is only half the problem; the other half is what we send once
we have it.

- **Few, and grouped.** At most one notification per type per day by
  default. "New episodes" is a single daily summary ("4 new episodes from
  The Rest Is Politics, Hard Fork and 2 more"), never one per episode.
- **Respect the user's day.** Use the time zone from #84, and never send
  outside 08:00–21:00 local time unless it is a briefing the user scheduled
  for that time.
- **Each notification opens the thing it is about** (the briefing, the
  episode, the inbox), never the home page.
- **Off when the user can see it already.** If the app is open and visible
  when a briefing is cut, update the page instead of notifying.
- **Easy to undo.** Every notification type can be switched off in
  Settings; the notification's long-press menu ("Turn off notifications"
  on Android) behaves the same as the toggle.

---

## 4. Capability catalogue

Each entry covers what it is, what it would give a Thestill user, support,
pros, cons, rough cost (S: under a day, M: a few days, L: a week or more)
and a verdict.

### 4.1 Installable web app (manifest, icons, meta tags)

**What:** `manifest.webmanifest` with name, icons (192, 512, maskable),
`display: standalone`, `start_url: /inbox`, `theme_color`; plus
`apple-touch-icon`, `apple-mobile-web-app-*` and `theme-color` meta tags,
and a real favicon.

**For the user:** Thestill on the Home Screen with a proper icon, opening
full-screen without Safari or Chrome UI, coloured status bar, its own
entry in the app switcher.

**Support:** iOS (manual install), Android (prompted install).

**Pros:**

- Prerequisite for push, badges, share target, shortcuts and reliable
  storage on iOS (see §2).
- Cheap, static, low risk. #80 already designs the root static serving for
  manifest and icons.
- Fixes the default `vite.svg` favicon, which matters even for desktop
  tabs.

**Cons:**

- Standalone mode has no browser back button or URL bar. Every screen
  needs a visible way back, and sharing a URL needs our own button (we
  have one).
- Standalone apps on iOS keep their own cookie store, separate from
  Safari. A user signed in in Safari has to sign in again in the
  installed app, and Google sign-in redirects need checking in
  standalone mode.
- Needs a real icon set (design work) and dark/light `theme-color`.

**Cost:** S (plus icon design). **Verdict:** Do first, as #80 Phase 1 or
pulled out of #80 if share target slips.

### 4.2 Web Push notifications

**What:** A service worker subscribes the device (VAPID keys); the backend
stores one subscription per user and device, and sends via the browser's
push service when something happens.

**For the user:** "Your Thursday briefing is ready" on the lock screen at
08:00. Optionally a daily new-episodes summary, or "your imported episode
is ready to read".

**Support:** Android everywhere; iOS 16.4+ Home Screen apps only.

**Pros:**

- The briefing is a scheduled, once-a-day product (#50/#84); a
  notification at the moment it is ready is the most natural channel it
  has. Email (#51) is good for reading at a desk, weaker on the move.
- The backend already has the structure: `briefing_deliveries` has a
  `channel` column with only `email` today, so push becomes a second
  channel with the same send-once rule and retry state machine.
- Declarative Web Push (iOS 18.4+) makes iOS delivery more robust: the
  notification can be shown even if our service worker code fails.

**Cons:**

- iOS users must install first. A good share of iPhone users will never
  see the feature.
- Every push must show a visible notification (`userVisibleOnly`). No
  silent background refreshes; iOS revokes subscriptions that break this.
- New backend surface: subscription table, VAPID key in config, a sender
  (for example `pywebpush`), cleanup of expired subscriptions (`404`/`410`
  from the push service), per-device preferences.
- Testing is awkward: real devices, real push services, iOS only on
  installed apps.
- The biggest risk is product, not engineering: one notification too many
  and users turn it off for good (§3.5).

**Cost:** M–L (service worker, subscription API, sender, Settings UI,
preferences). **Verdict:** Worth doing for "briefing ready" only at first,
with the §3 UX. Add other types once we see how people use the first one.

### 4.3 App icon badge

**What:** `navigator.setAppBadge(n)` / `clearAppBadge()`.

**For the user:** The Home Screen icon shows unread inbox count, or a dot
when a new briefing is waiting.

**Support:** Android (installed, no permission). iOS 16.4+ Home Screen
apps, only with notification permission granted.

**Pros:**

- Tiny API; can be updated whenever the inbox unread count already comes
  back from the API, and from a push handler.
- A quiet signal: no sound, no banner, which matches the §3 tone.

**Cons:**

- On iOS it rides on notification permission, so it is only as available
  as push.
- Counts on an icon are their own nag: an inbox of 140 unread episodes
  showing "140" is noise and makes people feel behind. Needs a choice:
  count, dot only, or off, probably defaulting to "new since last opened"
  rather than total unread.
- Badges go stale if only updated while the app is open; to be accurate
  they need push (or Periodic Background Sync on Android).

**Cost:** S once push exists. **Verdict:** Do alongside push, with a
setting and a "new briefing / new since last visit" default rather than a
raw unread count.

### 4.4 Service worker: app shell and offline reading

**What:** A service worker that caches the built JS/CSS (the app shell)
and, with network-first rules, recently opened API responses (inbox,
summaries, transcripts).

**For the user:** The app opens instantly, including on a flaky
connection, and an episode you opened earlier can be read on the Tube or
on a plane.

**Support:** Both platforms.

**Pros:**

- Faster cold start, especially in standalone mode where there is no
  browser cache warm-up.
- Reading summaries and transcripts offline fits how Thestill is used.
- Required anyway for push (§4.2).

**Cons:**

- Service workers are the classic source of "stuck on an old version"
  bugs. Needs a deliberate update strategy (for example a "New version
  available, reload" toast, and never caching `index.html` cache-first).
- Cached API responses are per user; logout must clear them, and shared
  devices must never show the previous user's inbox.
- Interacts with auth cookies, CSP (`worker-src`) and the Vite build
  (hashed asset names, a plugin such as `vite-plugin-pwa` or a small
  hand-written worker).
- Offline state means UI work: what does "Mark as played" do offline?
  Read-only offline is a sensible first cut.

**Cost:** M for shell plus read-only offline. **Verdict:** Yes, but keep
the first version minimal: shell caching plus push handling, then
offline reading as a follow-up.

### 4.5 Offline listening (downloaded audio)

**What:** Store episode audio in the Cache API or OPFS so it plays without
a connection.

**For the user:** "Download for the flight", like every podcast app.

**Support:** Technically both; practically difficult on iOS.

**Pros:**

- A real podcast-app expectation; the most-asked-for offline feature in
  any listening product.

**Cons:**

- Audio elements make HTTP range requests; a service worker has to answer
  them from a cached full file. Doable, but fiddly, and iOS Safari has had
  bugs here.
- Hour-long episodes are 50–100 MB each. Quotas are large but not
  unlimited, and iOS browser-tab storage is wiped after 7 days unused.
- Needs download management UI (progress, storage used, delete).
- Thestill's distinctive value is reading and briefings; the user's
  regular podcast app already does offline audio well (the screenshot
  that prompted this spec is Apple Podcasts).

**Cost:** L. **Verdict:** Not now. Revisit if usage shows people listening
in Thestill rather than reading.

### 4.6 Share target (incoming shares)

Fully covered by [#80](80-share-to-thestill.md): Android share-sheet entry,
iOS via an Apple Shortcut, clipboard-aware import. Listed here for
completeness. #80 needs §4.1 and does not need a service worker.

**Verdict:** Proceed as #80 describes.

### 4.7 Manifest shortcuts

**What:** `shortcuts` in the manifest: up to four entries shown on
long-press of the app icon.

**For the user:** Long-press the icon → "Today's briefing", "Continue
listening", "Inbox", "Import a link".

**Support:** Android only (and desktop Chrome/Edge). iOS ignores them.

**Pros:**

- Pure manifest JSON pointing at existing routes. Near zero cost.
- "Continue listening" pairs well with #90.

**Cons:**

- Android only.
- Static URLs, so "Continue listening" has to be a route that works out
  what to resume.

**Cost:** S. **Verdict:** Yes, alongside §4.1, once #90 lands.

### 4.8 Screen Wake Lock

**What:** `navigator.wakeLock.request('screen')` keeps the screen on.

**For the user:** Following the karaoke transcript (#23) while listening
without the phone locking every 30 seconds.

**Support:** Android; iOS Safari 16.4+ (Home Screen apps only reliable
from iOS 18.4).

**Pros:**

- A few lines, no permission prompt.
- Directly fixes an annoyance in the follow-along reading mode.

**Cons:**

- Drains battery if held when not needed. Must only be held while
  playing **and** follow-playback is on **and** the page is visible, and
  released otherwise (browsers release it on visibility change; we must
  re-acquire on return).
- Some users want the screen to lock while listening. Tie it to the
  follow-playback toggle, never to playback alone.

**Cost:** S. **Verdict:** Yes, cheap win.

### 4.9 AirPlay and Cast from inside the player

**What:** An AirPlay button in our player
(`webkitShowPlaybackTargetPicker()` on Safari) and the Remote Playback API
(`media.remote.prompt()`) for Cast on Chrome.

**For the user:** Send the episode to a HomePod, Apple TV, Sonos or
Chromecast from the Now Playing sheet.

**Support:** AirPlay on Safari (iOS and macOS); Cast via Remote Playback
on Chrome Android.

**Pros:**

- Small amount of code; shows only when a target is available (both APIs
  report availability).
- Video episodes (#62) make AirPlay to a TV genuinely useful.

**Cons:**

- On iOS, AirPlay is already reachable from the lock screen and Control
  Centre (the AirPods icon in the screenshot), so the in-app button is a
  convenience, not a capability.
- Remote playback hands the URL to the receiver: our audio URLs must be
  reachable without our session cookie, or casting fails. Needs checking
  against how audio is served.
- YouTube rendition is excluded (the iframe player has its own cast
  behaviour).
- Transcript sync while casting depends on getting reliable `currentTime`
  back from the receiver.

**Cost:** S–M. **Verdict:** Nice to have; do after the items above.

### 4.10 Media Session extensions

The lock-screen player already works (#61). Possible additions:

| Idea | Pros | Cons |
|---|---|---|
| `previoustrack` / `nexttrack` | Skip within a queue or a briefing's episode list from the lock screen and headphones | We have no queue yet. On iOS, registering next/previous may replace the ±15 s buttons, so it needs device testing and a deliberate choice. |
| Multiple artwork sizes (96–512) instead of one entry hard-coded as `512x512` | Sharper artwork on car displays, watches and Android notifications | Needs the image pipeline to produce sizes, or honest `sizes` values |
| Chapters (`chapterInfo`) from transcript sections | Lock-screen chapter list | Experimental, Chrome only; skip for now |
| Lock-screen support for the YouTube rendition | Consistent experience | Not controllable from our side; the answer is to prefer the native engine on phones when backgrounding matters |

**Verdict:** Fix the artwork sizes when convenient; add next/previous with
a queue or briefing playback, not before.

### 4.11 Background Sync, Periodic Background Sync, Background Fetch

**What:** Let the service worker retry failed writes later (Background
Sync), wake periodically to fetch new data (Periodic Background Sync), or
run long downloads after the page closes (Background Fetch).

**For the user:** Progress and "mark as played" saved even when the
connection dropped; the morning briefing already loaded when the app
opens.

**Support:** Chromium only; Periodic Sync only for installed apps with
enough engagement and at the browser's chosen frequency. Nothing on iOS.

**Pros:**

- Background Sync would make #90's progress writes reliable on flaky
  connections.

**Cons:**

- No iOS support, so we always need the foreground path anyway.
- Periodic Sync timing is not ours to set; can't promise "ready at
  08:00".
- Background Fetch only matters for offline audio (§4.5), which is
  deferred.

**Cost:** M. **Verdict:** Not now. #90's keepalive flush covers most of
the value; revisit with offline mode.

### 4.12 Persistent storage

**What:** `navigator.storage.persist()` asks the browser not to evict our
storage.

**Support:** Both (Safari 17+). Chrome grants or denies silently based on
engagement; Firefox shows a prompt.

**Pros:** One line; protects offline caches once they exist.

**Cons:** Firefox's prompt conflicts with §3, so call it only from a
user action (for example turning on offline reading). Pointless until
there is something worth keeping.

**Cost:** S. **Verdict:** Bundle with §4.4 when offline reading ships.

### 4.13 Considered and rejected

| Capability | Why not |
|---|---|
| Vibration API | No use case; Android only. |
| Contact Picker, File Handling, Web NFC, Bluetooth | No use case. |
| `registerProtocolHandler` (for example `web+podcast:`) | Almost no app links with custom schemes; share target (#80) covers incoming links. |
| Install banner on first visit | Contradicts §3. |
| Smart App Banner | Only for native App Store apps. |
| Notification Triggers (scheduled local notifications) | Chrome abandoned the origin trial; push covers the use case. |
| Window Controls Overlay, Tabbed display mode | Desktop-only polish; not a priority. |

---

## 5. Summary table

| # | Capability | Value to users | Cost | iOS | Android | Verdict |
|---|---|---|---|---|---|---|
| 4.1 | Installable app | High (gate for most of the rest) | S | Manual | ✅ | **Do first** |
| 4.2 | Push (briefing ready) | High | M–L | Installed only | ✅ | **Do, opt-in only (§3)** |
| 4.3 | Icon badge | Medium | S after push | With push permission | ✅ | With push, configurable |
| 4.4 | Service worker shell + offline reading | Medium | M | ✅ | ✅ | Yes, minimal first |
| 4.5 | Offline audio | Medium–low for Thestill | L | Fiddly | ✅ | Defer |
| 4.6 | Share target | High | (#80) | Shortcut | ✅ | Per #80 |
| 4.7 | Manifest shortcuts | Low–medium | S | ❌ | ✅ | With 4.1 |
| 4.8 | Screen Wake Lock | Medium (readers) | S | ✅ | ✅ | **Quick win** |
| 4.9 | AirPlay / Cast button | Low–medium | S–M | ✅ | ✅ | Later |
| 4.10 | Media Session extras | Low | S | ✅ | ✅ | Artwork sizes now; tracks later |
| 4.11 | Background sync family | Low | M | ❌ | ✅ | Defer |
| 4.12 | Persistent storage | Low until offline | S | ✅ | ✅ | With 4.4 |

---

## 6. Suggested order

1. **Quick wins, no permissions:** Screen Wake Lock in follow-along mode
   (§4.8); Media Session artwork sizes (§4.10).
2. **Installable app** (§4.1) with manifest shortcuts (§4.7), as #80
   Phase 1 or pulled ahead of it. Settings gains "Install app" / "Add to
   Home Screen" (§3.4).
3. **Service worker, minimal:** shell caching with a safe update path
   (§4.4). No offline API caching yet.
4. **Push for "briefing ready"** (§4.2) as a second #51 delivery channel,
   with the Settings → Notifications section and the one-time inline offer
   on the briefing card (§3.2). Badge (§4.3) in the same release.
5. **Offline reading** of recently opened episodes, plus persistent
   storage (§4.4, §4.12).
6. Re-assess: more push types, AirPlay/Cast, offline audio, queue-driven
   next/previous.

Each step should get its own spec or a phase in an existing one (#80 for
step 2, #51 for step 4) before it is built.

---

## 7. Open questions

1. **Push and email together.** If a user has both on, do they get both
   for the same briefing? Proposed: yes, they are separate opt-ins, but
   the push copy should not duplicate the email; or offer "push instead of
   email" as a single choice.
2. **Inline offer threshold.** Three briefing opens on different days, or
   something else? Should the offer also appear after the first scheduled
   briefing is set up in #84?
3. **Badge default.** Dot for a new briefing, "new since last visit"
   count, or off until switched on?
4. **Per-device or per-account preferences.** Notification toggles are
   naturally per device (a laptop and a phone differ). Should "New
   episodes" be account-wide with per-device delivery instead?
5. **Standalone sign-in.** Does Google sign-in complete inside an iOS
   standalone web app today, or does it bounce to Safari and lose the
   session? Needs a device test before §4.1 ships.
6. **Audio URL access for casting.** Are episode audio URLs reachable
   without the session cookie? Determines whether §4.9 works as described.

---

## 8. Changelog

| Date | Change |
|---|---|
| 2026-10-01 | Created: capability inventory, platform constraints, permission-prompting principles, pros and cons per capability, suggested order |
