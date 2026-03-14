# Thestill Design System

This document describes the visual design system used in the Thestill web application. It serves as a reference for UX/UI review and redesign.

## Technology Stack

- **Framework**: React 18.3 + TypeScript 5.6
- **Styling**: Tailwind CSS 3.4 (utility-first, no component library)
- **Icons**: Inline SVGs (Heroicons style, outline variant)
- **Typography**: Inter (sans), JetBrains Mono (code)
- **Build**: Vite 6.0

## Color Palette

### Brand Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `primary-50` | Light tint | Hover backgrounds |
| `primary-100` | Light tint | Gradient fills, badge backgrounds |
| `primary-400` | Medium | Icon fills on light backgrounds |
| `primary-600` | `#2b6cb0` | Links, active tabs, primary buttons |
| `primary-900` | `#1a365d` | Active nav items, logo background, headings |
| `secondary-50` to `secondary-900` | Orange scale | Accent, gradient endpoints |

### Semantic Colors

| Purpose | Color | Tailwind Token |
|---------|-------|----------------|
| Success | Green `#48bb78` | `green-100/500/600/700` |
| Warning | Orange/Yellow | `yellow-100/600/700`, `orange-100/600/700` |
| Error | Red `#e53e3e` | `red-50/100/200/600/700` |
| Info | Blue | `blue-100/600/700` |
| Neutral | Gray | `gray-50` through `gray-900` |

### Pipeline Stage Colors

Each processing stage has a unique color for visual tracking:

| Stage | Badge | Bar Color | Button |
|-------|-------|-----------|--------|
| Discovered | `gray-100/600` | `gray-400` | - |
| Downloaded | `blue-100/700` | `blue-400` | `blue-600` |
| Downsampled | `indigo-100/700` | `indigo-400` | `indigo-600` |
| Transcribed | `purple-100/700` | `purple-400` | `purple-600` |
| Cleaned | `amber-100/700` | `amber-400` | `amber-600` |
| Summarized (Ready) | `green-100/700` | `green-400` | `green-600` |

### Failure Type Colors

| Type | Badge |
|------|-------|
| Fatal | `red-100/700` with `red-200` border |
| Transient | `yellow-100/700` with `yellow-200` border |

### Digest Status Colors

| Status | Badge |
|--------|-------|
| Pending | `yellow-100/700` |
| In Progress | `blue-100/700` |
| Completed | `green-100/700` |
| Partial | `orange-100/700` |
| Failed | `red-100/700` |

## Typography

| Element | Style |
|---------|-------|
| Page title (h1) | `text-2xl font-bold text-gray-900` (24px, 700) |
| Section title (h2) | `text-lg font-semibold text-gray-900` (18px, 600) |
| Card title | `font-semibold text-gray-900` or `font-medium text-gray-900` |
| Body text | `text-base text-gray-900` (16px) |
| Secondary text | `text-sm text-gray-500` (14px) |
| Meta / timestamp | `text-xs text-gray-400` (12px) |
| Subtitle / description | `text-gray-500 mt-1` or `text-gray-600` |
| Links | `text-primary-600 hover:underline` or `text-blue-600` |

## Spacing

Standard Tailwind 4px grid:

| Context | Value |
|---------|-------|
| Page padding | `p-4` (mobile), `p-6` (tablet), `p-8` (desktop) |
| Section spacing | `space-y-6` or `space-y-8` (24-32px) |
| Card padding | `p-4` (mobile), `p-6` (tablet/desktop) |
| Element gap | `gap-2` to `gap-4` (8-16px) |
| Badge padding | `px-2 py-0.5` (8px/2px) |
| Button padding | `px-3 py-2` (sm), `px-4 py-2` (md) |

## Borders & Surfaces

| Element | Style |
|---------|-------|
| Cards | `bg-white rounded-lg border border-gray-200` |
| Cards (hover) | `hover:border-gray-300 hover:shadow-md` or `hover:shadow-sm` |
| Selected card | `border-indigo-500 ring-2 ring-indigo-200` |
| Failed card (fatal) | `border-red-300 hover:border-red-400` |
| Failed card (transient) | `border-yellow-300 hover:border-yellow-400` |
| Active/in-progress card | `border-blue-300 bg-blue-50/30` |
| Modals | `bg-white rounded-xl shadow-xl` |
| Modal overlay | `fixed inset-0 bg-black/50 z-50` |
| Page background | `bg-gray-50` |
| Sidebar | `bg-white border-r border-gray-200` |
| Info boxes | `bg-gray-50 rounded-lg p-4` |
| Error containers | `bg-red-50 border border-red-200 rounded-lg p-6` |

## Component Patterns

### Buttons

Four variants with three sizes:

| Variant | Style |
|---------|-------|
| Primary | `bg-primary-600 text-white hover:bg-primary-700` |
| Secondary | `bg-gray-100 text-gray-700 hover:bg-gray-200` |
| Danger | `bg-red-600 text-white hover:bg-red-700` |
| Ghost | `text-gray-600 hover:bg-gray-100` |

| Size | Padding |
|------|---------|
| Small (`sm`) | `px-2.5 py-1.5 text-xs` |
| Medium (`md`) | `px-3.5 py-2 text-sm` |
| Large (`lg`) | `px-4 py-2.5 text-base` |

Buttons support: icons, loading spinner, disabled state, `iconOnlyMobile` (hides label on mobile).

### Split Button (Pipeline Action)

- Main button executes next step, chevron dropdown offers "Run Full Pipeline"
- Color matches the pipeline stage being triggered
- Rounded-left on main, rounded-right on dropdown trigger
- Separated by `border-l border-white/20`

### Badges

- Shape: `rounded-full` pill badges
- Size: `px-2 py-0.5 text-xs font-medium`
- Colored per context (state, status, failure type)

### Status Cards (Dashboard)

- White card with icon, label, numeric value, optional subtitle
- Color-coded icon area (primary, secondary, success, warning)
- Grid: 2 columns on tablet, 4 columns on desktop

### Stat Cards (Operational pages)

- Simpler: large `text-2xl font-bold` number + small label
- Grid: 2 columns mobile, 4 columns tablet+
- Color-coded number (blue, green, yellow, red, default gray)

### Episode Card

- Horizontal layout: optional checkbox + 40x40 artwork + content area
- Content: title (line-clamp-2), podcast name, state badge, failure badge, meta info, summary preview
- Linked to episode detail page
- Hover: subtle border and shadow change

### Podcast Card

- Vertical card: 64x64 artwork + title + description (line-clamp-2) + stats + progress bar
- Progress bar shows processing percentage (green fill on gray track)
- Linked to podcast detail page

### Tabs

- Underline style: `border-b-2` on active tab
- Active: `border-primary-600 text-primary-600`
- Inactive: `border-transparent text-gray-500 hover:text-gray-700`
- Full-width on mobile, auto-width on desktop

### Modals

- Centered overlay with `bg-black/50` backdrop
- Content: `max-w-lg` or `max-w-2xl`, `rounded-xl shadow-xl`
- Header with title + close button
- Footer with Cancel + primary action buttons
- Scrollable body with `max-h-[90vh] overflow-y-auto`

### Toast Notifications

- Fixed position bottom-right
- Auto-dismiss after 5 seconds
- Slide-in animation
- Three types: success (green), error (red), info (blue)

### Empty States

- Centered icon (gray, 48x48) + title + description + optional CTA button
- Inside white bordered card

### Loading States

- Skeleton: `animate-pulse` gray rectangles matching content layout
- Spinners: `animate-spin` circular border (primary-600 or stage color)
- Full-page loading: centered spinner

### Progress Bars

- Track: `h-2 bg-gray-100 rounded-full` (or `h-1.5` for smaller)
- Fill: colored `rounded-full transition-all duration-300/500`
- Optional label + percentage text

### Breadcrumbs

- `text-sm` with `/` separator (`text-gray-400`)
- Links: `text-gray-500 hover:text-gray-700`
- Current page: `text-gray-900`
- Truncation on mobile with `max-w-[120px]`

### Artwork/Thumbnails

| Context | Size | Style |
|---------|------|-------|
| Episode card | 40x40 | `rounded-md object-cover` |
| Podcast card | 64x64 | `rounded-lg object-cover` |
| Detail header (mobile) | 80x80 | `rounded-lg object-cover mx-auto` |
| Detail header (desktop) | 96x96 | `rounded-lg object-cover` |
| Fallback | Same | Gradient `from-primary-100 to-secondary-100` + microphone icon |

## Responsive Breakpoints

| Name | Width | Behavior |
|------|-------|----------|
| Mobile | < 640px | Single column, hamburger menu, mobile header, bottom-aligned content |
| Tablet | 640-1023px | Collapsed sidebar (64px icons only, expandable), 2-column grids |
| Desktop | >= 1024px | Full sidebar (256px with labels), 3-4 column grids |

## Navigation Structure

### Sidebar (Tablet/Desktop)

- Fixed left, full height
- Desktop: 256px wide, always expanded with labels
- Tablet: 64px collapsed (icon-only), expandable to 256px with overlay
- Logo at top ("ts" icon collapsed, "Thestill" + tagline expanded)
- 6 nav items with icons
- User menu at bottom (border-top separator)
- Active item: `bg-primary-900 text-white rounded-lg`
- Inactive item: `text-gray-600 hover:bg-gray-100`

### Mobile Header + Drawer

- Fixed top header (56px) with hamburger + "Thestill" title
- Full-screen navigation drawer overlay on menu open
- Same 6 nav items as sidebar

### Navigation Items

1. Dashboard (home icon)
2. Podcasts (microphone icon)
3. Episodes (archive icon)
4. Digests (document icon)
5. Failed Tasks (warning triangle icon)
6. Task Queue (list icon)

## Animations & Transitions

| Animation | Usage |
|-----------|-------|
| `animate-pulse` | Skeleton loading states |
| `animate-spin` | Loading spinners |
| `animate-slide-in` | Toast notifications |
| `transition-all duration-200` | Button hover, sidebar expand |
| `transition-all duration-300` | Sidebar width, progress bars |
| `transition-all duration-500` | Pipeline bar segments |
| `transition-colors` | Links, nav items, badges |
| `transition-shadow` | Card hover effects |

## Scrolling Patterns

- **Infinite scroll**: Intersection Observer triggers load when sentinel enters viewport (100px rootMargin)
- **Reading position**: Auto-saved and restored for episode detail (localStorage)
- **Custom scrollbar**: Webkit-styled thin scrollbar (gray track `#f1f1f1`, darker thumb `#c1c1c1`)

## Data Refresh

- All data auto-refreshes every 5 seconds (React Query polling)
- Stale time: 5 seconds
- Single retry on failure
- Manual refresh button on Dashboard (triggers feed discovery)
