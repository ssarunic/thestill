# thestill/web/frontend Blueprint

## System Overview

The thestill web frontend: a React 19 + TypeScript single-page app built with Vite and styled with Tailwind 4, served by the FastAPI backend from `../static`. It talks only to the backend's `/api` routes (cookie session) and renders two experiences from one bundle: the signed-in app (inbox, reader, player, search, briefings, settings, operator and admin views) and, for signed-out visitors in multi-user mode, the public site (spec #94) with server-written first loads (spec #105). Data fetching uses TanStack Query; routing uses react-router-dom 7; usage events use posthog-js through a same-origin proxy.

## Local Interface & Invariants

- npm scripts: `dev` (vite), `build` (`tsc -b && vite build`), `lint` (`eslint .`), `test` / `test:run` / `test:coverage` (vitest), `test:e2e` / `test:e2e:ci` (Playwright; CI excludes `@live`) [package.json:scripts]
- `index.html` — SPA shell; the server fills the `<!--thestill:head-->` and `<!--thestill:body-->` markers for prerendered public pages; entry `/src/main.tsx` [index.html]
- Invariant: the build writes to `../static` (`outDir`, `emptyOutDir: true`) and splits vendors into `vendor-markdown`, `vendor-query`, `vendor-react` [vite.config.ts:build]
- Invariant: the dev server (port 5173) proxies `/api` to `http://localhost:8000` [vite.config.ts:server]
- Invariant: unit tests run in jsdom with `src/test/setup.ts`, collecting only `src/**/*.{test,spec}.{ts,tsx}`; coverage excludes `src/test/**` and test files [vitest.config.ts:test]
- Invariant: TypeScript is `strict` with `noUnusedLocals`, `noUnusedParameters`, `noUncheckedSideEffectImports`, bundler resolution, `noEmit`; test files are excluded from `tsc` [tsconfig.json:compilerOptions]
- Invariant: ESLint applies `@eslint/js` + `typescript-eslint` recommended, `react-hooks` recommended, and `react-refresh/only-export-components` (warn) to `src/**/*.{ts,tsx}`; `dist/`, `static/`, `node_modules/`, `coverage/` are ignored [eslint.config.js]
- Invariant: Playwright tests live in `./tests`, Chromium only, against `PLAYWRIGHT_BASE_URL` or a managed `npm run preview` on `http://localhost:4173` (needs a prior `npm run build`) [playwright.config.ts:webServer]
- `public/sw.js` — spec #116 service worker (plain JavaScript, no build step): answers only cross-origin CORS-mode audio/video requests, returns the original response, caches nothing, and posts `drift-media` reports (block digests of what the element is served) to the page; `drift-disable` stands it down and `drift-ping` is answered with `drift-pong` [public/sw.js]
- `public/help/*.md` — help pages fetched at runtime (`/claude`), with `public/legal/operator.json` placeholders [src/pages/public/Help.tsx:Help]
- Invariant: Tailwind scans `./index.html` and `./src/**/*.{js,ts,jsx,tsx}`; PostCSS runs `@tailwindcss/postcss` and `autoprefixer` [tailwind.config.js:content]

## Subdirectories & System Boundaries

- src: [./src/EGM.md](./src/EGM.md) - application source: entry and route trees, API clients and types, contexts (auth, player), hooks, components, pages (app, admin, public), analytics, drift alignment and its worker, constants and utils

## Global Invariants

- The frontend never talks to a third party for data: all reads and writes go to same-origin `/api` (plus `/ingest` for usage events and the YouTube IFrame API script for YouTube playback). The exception is the episode audio itself: the media element plays it from the publisher, and with spec #116 `drift.align` the drift worker downloads it again (`mode: 'cors'`, no credentials) [src/api/client.ts:API_BASE]
- Authorization decisions mirror server flags (`isAdmin`, `can('transcript.edit')`, `isMultiUser`); the server enforces them again [src/contexts/AuthContext.tsx:can]
- Navigation state (filters, tabs, deep links) lives in the URL so Back restores it [src/hooks/useUrlParams.ts:useUrlParams]

## Global Side Effects

- Build output replaces `thestill/web/static` on every `npm run build` [vite.config.ts:build]
- Registers the `/sw.js` service worker (scope `/`) only for sessions holding `drift.align` in a supported browser [src/drift/swClient.ts:ensureDriftServiceWorker]
- Browser storage under `thestill:`-prefixed keys, `reading-position-*` and `thestill.pendingFollow`; no analytics storage [src/analytics/client.ts:stopAnalytics]

## System Verification

- Run: `cd thestill/web/frontend && npm run test:run`
- Run: `cd thestill/web/frontend && npm run lint`
- Do not run `npm install` or `npm audit fix` here (they corrupt the cross-platform lockfile); `npm ci` is safe.
