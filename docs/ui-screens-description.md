# Thestill Web UI - Screen-by-Screen Description

This document describes every screen in the Thestill web application, intended for UX/UI designer review. For the visual design system (colors, typography, component patterns), see [ui-design-system.md](ui-design-system.md).

## Table of Contents

1. [Application Overview](#application-overview)
2. [User Flows (Storyboard)](#user-flows-storyboard)
3. [Global Shell](#global-shell)
4. [Login Page](#1-login-page)
5. [Dashboard](#2-dashboard)
6. [Podcasts List](#3-podcasts-list)
7. [Podcast Detail](#4-podcast-detail)
8. [Episode Detail](#5-episode-detail)
9. [Episodes Browser](#6-episodes-browser)
10. [Digests List](#7-digests-list)
11. [Digest Detail](#8-digest-detail)
12. [Failed Tasks](#9-failed-tasks)
13. [Task Queue](#10-task-queue)

---

## Application Overview

**Thestill** is an automated podcast transcription and summarization pipeline. Users follow podcasts, and the system processes episodes through a 6-stage pipeline:

```
Discovered -> Downloaded -> Downsampled -> Transcribed -> Cleaned -> Summarized (Ready)
```

The web UI allows users to:

- Follow/unfollow podcast feeds (RSS, Apple Podcasts, YouTube)
- Monitor the processing pipeline
- Read transcripts and AI-generated summaries
- Generate digest documents that compile multiple episode summaries
- Monitor and manage failed/queued background tasks

**User profile**: Power users managing a personal podcast knowledge pipeline. Technical enough to understand pipeline concepts, but the UI should make monitoring and reading effortless.

---

## User Flows (Storyboard)

### Flow 1: First-Time Setup

```
Login (if multi-user)
  -> Dashboard (empty state)
    -> Click "Follow" on Podcasts page (or prompted from empty state)
      -> Add Podcast Modal: enter RSS/Apple/YouTube URL
        -> Toast: "Following podcast..."
        -> Toast: "Following: Podcast Name (N episodes)"
          -> Podcasts page now shows the new podcast card
```

### Flow 2: Daily Catch-Up (Primary Flow)

```
Dashboard
  -> See Morning Briefing widget: "5 episodes ready for digest"
    -> Click "Quick Catch-Up"
      -> Toast: "Digest created with 5 episodes"
        -> Navigate to Digests -> View latest digest
          -> Read compiled summary of all new episodes
```

### Flow 3: Processing a New Episode

```
Dashboard (see "Pending" count)
  -> Navigate to Podcasts -> Select podcast -> Select episode
    -> Episode Detail: state = "Discovered"
      -> Click "Download" (or "Run Full Pipeline" from dropdown)
        -> Watch progress: Download -> Downsample -> Transcribe (with SSE progress bar) -> Clean -> Summarize
          -> State becomes "Ready"
            -> Switch to Summary tab to read the AI summary
            -> Switch to Transcript tab to read the cleaned transcript
```

### Flow 4: Browsing and Filtering Episodes

```
Episodes page
  -> Search by title
  -> Filter by podcast, state, date range
  -> Sort by date/title/recently updated
  -> Select multiple episodes with checkboxes
    -> Bulk Actions Bar appears at bottom
      -> "Process Selected" to queue pipeline tasks
```

### Flow 5: Handling Failures

```
Dashboard (or sidebar badge on "Failed Tasks")
  -> Navigate to Failed Tasks page
    -> See failed tasks with error type (Transient/Fatal)
      -> Expand error message for details
      -> Click "Retry" on transient errors
      -> Click "Skip" on unrecoverable errors
      -> Or select multiple + "Retry All"
```

### Flow 6: Monitoring the Queue

```
Task Queue page
  -> See worker status (Running/Stopped)
  -> See currently processing task with spinner
  -> See pending tasks in order
    -> "Bump" a task to move it to front of queue
    -> "Cancel" a task to remove it
  -> See retry-scheduled tasks with countdown timer
  -> See recently completed tasks
```

### Flow 7: Creating a Custom Digest

```
Digests page
  -> Click "New Digest"
    -> Create Digest Modal:
      -> Set time window (days)
      -> Set max episodes
      -> Toggle "Ready only" / "Exclude already digested"
      -> Click "Preview selection" to see matching episodes
      -> Click "Create Digest"
        -> Card appears with progress bar while processing
          -> Click "View" when completed
            -> Read compiled digest content
```

---

## Global Shell

### Layout Structure

```
+--------------------------------------------------+
| [Sidebar / Mobile Header]                         |
+------+-------------------------------------------+
|      |                                           |
| NAV  |              MAIN CONTENT                 |
|      |                                           |
| Logo |   (page-specific content rendered here)   |
|      |                                           |
| ---  |                                           |
| Dash |                                           |
| Pods |                                           |
| Eps  |                                           |
| Dig  |                                           |
| Fail |                                           |
| Queue|                                           |
| ---  |                                           |
| User |                                           |
+------+-------------------------------------------+
```

### Desktop (>= 1024px)

- **Sidebar**: Fixed left, 256px wide, always visible
  - Top: "Thestill" brand name + "Podcast Transcription" tagline
  - Middle: 6 navigation links with icons and labels
  - Bottom: User menu (avatar/initials + name + dropdown)
  - Active nav item has dark blue (`primary-900`) background with white text
- **Main content**: Offset 256px left margin, padded 32px

### Tablet (640-1023px)

- **Sidebar**: Fixed left, 64px wide (icons only)
  - Shows "ts" logo icon instead of full brand name
  - Hamburger button to expand to 256px overlay
  - When expanded: dark overlay behind, close button at top-right
- **Main content**: Offset 64px left margin, padded 24px

### Mobile (< 640px)

- **Fixed header**: 56px tall, hamburger menu button + "Thestill" title
- **Navigation drawer**: Full-screen overlay with same nav items
- **Main content**: No left margin, top padding 56px for header, padded 16px

---

## 1. Login Page

**Route**: `/login`
**Visibility**: Only in multi-user mode (Google OAuth). In single-user mode, users go directly to Dashboard.

### Layout

```
+------------------------------------------+
|                                          |
|              [ts logo icon]              |
|              Thestill                    |
|    Podcast Transcription & Summarization |
|                                          |
|    +--------------------------------+    |
|    |     Sign in to continue        |    |
|    |                                |    |
|    | [Google icon] Continue with    |    |
|    |               Google           |    |
|    |                                |    |
|    | Terms of Service notice        |    |
|    +--------------------------------+    |
|                                          |
|    "Secure authentication powered by     |
|     Google OAuth"                        |
+------------------------------------------+
```

### Behavior

- Full-screen centered layout on `bg-gray-50`
- Auto-redirects to Dashboard if already authenticated or in single-user mode
- Loading spinner shown while checking auth status
- Single "Continue with Google" button triggers OAuth redirect

---

## 2. Dashboard

**Route**: `/`

### Layout

```
+--------------------------------------------------+
| Dashboard                        [Refresh Button] |
| Overview of your podcast processing pipeline      |
+--------------------------------------------------+

MOBILE ONLY:
+--------------------------------------------------+
| [Mobile Summary Bar: 4 inline stats]              |
+--------------------------------------------------+

TABLET/DESKTOP:
+------------+------------+------------+------------+
| Podcasts   | Total      | Processed  | Pending    |
| Tracked    | Episodes   | (% done)   |            |
|    12      |    345     |  280 (81%) |    65      |
+------------+------------+------------+------------+

+--------------------------------------------------+
| Morning Briefing         [Quick Catch-Up] [View All]
| (sun icon)                                        |
| 5                                                 |
| episodes ready for digest                         |
| Latest: [completed] (3/3)                         |
+--------------------------------------------------+

HIDDEN ON MOBILE:
+--------------------------------------------------+
| Pipeline Status                                   |
| [========================================] bar    |
| Legend: Discovered: 5  Downloaded: 10  ...        |
+--------------------------------------------------+

+--------------------------------------------------+
| Recent Activity                                   |
| - Episode "X" transcribed           2 min ago     |
| - Episode "Y" downloaded            5 min ago     |
| - Podcast "Z" added                 1 hour ago    |
|                    [Load More]                    |
+--------------------------------------------------+
```

### Components & Behavior

**Status Cards** (hidden on mobile, replaced by compact summary bar):

- 4 cards in a grid: Podcasts Tracked, Total Episodes, Processed (with % subtitle), Pending
- Each has a colored icon, label, and numeric value
- Shows "..." while loading

**Morning Briefing Widget**:

- Gradient background (indigo-to-purple) with sun icon
- Shows count of episodes ready for digest
- "Quick Catch-Up" button: creates a morning briefing digest in one click
- "View All" link: navigates to Digests page
- Shows latest digest status badge (pending/completed/etc.)
- Button disabled when count is 0 or creation is in progress

**Pipeline Status** (hidden on mobile):

- Stacked horizontal bar showing proportion of episodes in each pipeline stage
- Color-coded segments (gray -> blue -> indigo -> purple -> amber -> green)
- Legend grid below with stage name + count

**Recent Activity Feed**:

- Chronological list of recent events (downloads, transcriptions, podcast additions, etc.)
- Each item has: icon + description + relative timestamp
- Infinite scroll with "Load More" trigger via Intersection Observer

**Refresh Button**:

- Top-right corner
- Triggers feed discovery (checks RSS feeds for new episodes)
- Shows loading state while refreshing

---

## 3. Podcasts List

**Route**: `/podcasts`

### Layout

```
+--------------------------------------------------+
| Podcasts                            [+ Follow]    |
| 12 podcasts tracked                               |
+--------------------------------------------------+

+----------------+  +----------------+  +----------------+
| [artwork 64px] |  | [artwork 64px] |  | [artwork 64px] |
| Podcast Title  |  | Podcast Title  |  | Podcast Title  |
| Description... |  | Description... |  | Description... |
| 45 eps  30 done|  | 12 eps  8 done |  | 100 eps 95 done|
| [====75%====]  |  | [===67%===]    |  | [=======95%==] |
+----------------+  +----------------+  +----------------+

                    [spinner or "All podcasts loaded"]
```

### Grid Layout

- 1 column on mobile
- 2 columns on medium tablets (`md`)
- 3 columns on desktop (`lg`)

### Podcast Card

- Clickable card (links to Podcast Detail)
- Artwork (64x64 rounded, or gradient fallback with microphone icon)
- Title (truncated single line)
- Description (line-clamp-2)
- Stats row: episode count icon + count, processed icon + count
- Progress bar at bottom: green fill on gray track with "Processing progress" label + percentage

### Empty State

- Large microphone icon, "No podcasts followed" heading
- "Click the Follow button to get started" text
- "Follow Podcast" CTA button

### Follow Button

- Top-right, primary color
- On mobile: icon-only (plus icon)
- On desktop: icon + "Follow" label
- Opens Add Podcast Modal

### Add Podcast Modal

```
+--------------------------------------+
| Follow Podcast                   [X] |
+--------------------------------------+
| Podcast URL                          |
| [https://example.com/feed.xml    ]   |
| Supports RSS, Apple Podcasts,        |
| YouTube channels/playlists           |
+--------------------------------------+
|                    [Cancel] [Follow]  |
+--------------------------------------+
```

- URL input with validation
- Closes immediately on submit, shows "Following..." toast
- On success: "Following: Podcast Name (N episodes)" toast
- On error: "Failed to follow podcast: ..." error toast
- Escape key or backdrop click to close

### Infinite Scroll

- Loads 12 podcasts per page
- Spinner at bottom while loading more
- "All podcasts loaded" text when no more pages

---

## 4. Podcast Detail

**Route**: `/podcasts/:podcastSlug`

### Layout

```
Podcasts / Podcast Name                             (breadcrumb)

+--------------------------------------------------+
| [artwork    ] Podcast Title [Explicit] [Unfollow] |
|  96x96       By Author Name                      |
|              Description text (expandable)...     |
|              Category > Subcategory               |
|              345 episodes . 280 processed . link  |
|              Complete series . No new episodes    |
|              (c) Copyright notice                 |
+--------------------------------------------------+

Episodes (345)
+--------------------------------------------------+
| [art] Episode Title              [Discovered]     |
|       S01E05 . Nov 15, 2024 . 45 min             |
+--------------------------------------------------+
| [art] Episode Title              [Ready]          |
|       S01E04 . Nov 10, 2024 . 1h 20min           |
|       Summary preview text appears here...        |
+--------------------------------------------------+
                                        ... more ...
```

### Header Card

- **Artwork**: 80x80 mobile (centered), 96x96 desktop (left-aligned)
- **Title row**: Title + Explicit badge (if explicit) + Unfollow button (right-aligned)
- **Author**: "By Author Name" (if available)
- **Description**: Expandable with "Show more"/"Show less" toggle (3 lines default)
- **Meta row**: Category (with subcategory), episode count, processed count (green), website link (external)
- **Complete indicator**: "Complete series . No new episodes" (if podcast is marked complete)
- **Copyright**: Small gray text at bottom

### Unfollow Button

- Secondary variant, minus icon
- Icon-only on mobile
- Confirmation not required (immediate action)
- On success: toast + redirect to Podcasts list
- On error: error toast

### Episodes List

- Section heading: "Episodes (345)" with total count
- Vertical list of Episode Cards (see Episode Card pattern in design system)
- Each card shows: artwork, title, state badge, season/episode number, explicit badge, date, duration, failure info, summary preview
- Cards link to Episode Detail
- Infinite scroll with loading spinner

### Loading State

- Skeleton: artwork placeholder + text lines + progress bar placeholder
- Episode skeletons: circle + text lines

---

## 5. Episode Detail

**Route**: `/podcasts/:podcastSlug/episodes/:episodeSlug`

### Layout

```
Podcasts / Podcast Name / Episode Title            (breadcrumb)

+--------------------------------------------------+
| [artwork    ] Episode Title          [Ready]      |
|  96x96       Podcast Name                        |
|                                                   |
| S02E05 [Explicit] November 15, 2024 . 45 min    |
| . [Share] . [Show Notes ->]                      |
|                                                   |
| --- (separator) ---                               |
| [FAILURE BANNER - if failed]                      |
|                                                   |
| --- (separator) ---                               |
| [PIPELINE ACTION - if not ready]                  |
|   [Download v]  or  Pipeline stepper progress     |
|                                                   |
| --- (separator) ---                               |
| [Audio Player: native HTML5 audio]               |
|                                                   |
| --- (separator) ---                               |
| Description (expandable)                          |
+--------------------------------------------------+

+--------------------------------------------------+
| [Summary]  [Transcript]              (tab bar)   |
|--------------------------------------------------+
|                                                   |
| (Tab content: rendered markdown summary           |
|  or speaker-attributed transcript)                |
|                                                   |
+--------------------------------------------------+
```

### Header Card

- **Artwork**: Episode image (falls back to podcast image, then gradient placeholder)
  - 80x80 mobile centered, 96x96 desktop left-aligned
- **Title**: Bold, large
- **Podcast name**: Gray subtitle, links to podcast
- **State badge**: Colored pill, right-aligned (e.g., "Ready", "Discovered", "Transcribed")
- **Meta row**: Season/episode number, explicit badge, formatted date, duration, share button, show notes external link
- **Separators**: Light gray border-top between sections

### Failure Banner

- Shown only when episode is in failed state
- Red or yellow background depending on failure type
- Shows: failed stage, failure reason (truncated), retry button
- "View details" opens FailureDetailsModal

### Pipeline Action Button (Split Button)

- Shown only when episode is NOT in "Ready" (summarized) state and NOT failed
- **Idle state**: Split button with stage-colored main action + dropdown chevron
  - Main button: next step (e.g., "Download", "Transcribe", "Summarize")
  - Dropdown: "Next step" + "Run Full Pipeline" option
- **Queued state**: Gray badge with clock icon, "Stage: Queued (waiting for worker)"
- **Processing state**: Purple badge with spinner, "Stage: Processing..."
- **Transcribe with progress**: Detailed progress display
  - Stage label (Loading model, Transcribing, Aligning, Diarizing...)
  - Progress bar with percentage
  - ETA ("~2m 30s remaining")
- **Full pipeline mode**: Step-by-step stepper showing all remaining stages
  - Current stage highlighted with spinner
  - Completed stages with checkmark
  - Future stages grayed out
  - "Cancel Pipeline" link below
- **Retry scheduled**: Yellow banner with countdown timer
  - "Retry scheduled - Attempt 2/3 in 4m 30s"
  - Last error message preview
  - "Cancel" button

### Audio Player

- Native HTML5 `<audio>` element with browser controls
- External link button to open audio URL directly

### Content Tabs

- Two tabs: "Summary" and "Transcript"
- Default active tab: Summary
- Green dot indicator on tabs when content is available
- **Summary tab**:
  - Rendered markdown with prose styling
  - "Not yet available" message with context based on episode state
  - Loading skeleton while fetching
- **Transcript tab**:
  - Speaker-attributed text with color-coded speaker names
  - Timestamps (if available)
  - Markdown rendering within speaker segments
  - "Not yet available" message with state context

### Reading Position

- Scroll position automatically saved to localStorage per episode
- Restored on revisit

---

## 6. Episodes Browser

**Route**: `/episodes`

### Layout

```
+--------------------------------------------------+
| Episodes                                          |
| 345 episodes                                      |
+--------------------------------------------------+

+--------------------------------------------------+
| [Search episodes by title...                    ] |
| [All podcasts v] [All statuses v] From:[date]    |
| To:[date] [Newest first v] [X Clear filters]     |
+--------------------------------------------------+

[x] Select all visible (5 selected)

+--------------------------------------------------+
| [x] [art] Episode Title    Podcast Name  [Ready] |
|           S01E05 . Nov 15 . 45 min               |
+--------------------------------------------------+
| [ ] [art] Episode Title    Podcast Name  [Disc.] |
|           Nov 10 . 1h 20min                      |
+--------------------------------------------------+
                                        ... more ...

+==================================================+
| BULK ACTIONS:  5 selected  [Process] [Deselect]  |  (sticky bottom)
+==================================================+
```

### Filters Panel

- White card containing all filter controls
- **Search**: Text input with search icon, 300ms debounce
- **Podcast filter**: Dropdown populated from user's podcast list
- **State filter**: Dropdown with all pipeline states (Discovered through Ready)
- **Date range**: Two date inputs (From / To)
- **Sort**: Dropdown (Newest first, Oldest first, Title A-Z, Title Z-A, Recently updated)
- **Clear filters**: X button, only shown when any filter is active
- All filters sync to URL query parameters for shareability

### Episode Cards with Selection

- Each card has a checkbox on the left (outside the clickable link area)
- Clicking the card navigates to Episode Detail
- Clicking the checkbox selects/deselects
- Selected cards: indigo border + ring highlight
- Shows podcast name below episode title (unlike Podcast Detail which omits it)

### Select All

- Checkbox + "Select all visible" / "Deselect all" toggle
- Shows count of selected items

### Bulk Actions Bar

- Fixed to bottom of viewport when items are selected
- Shows: selected count, "Process Selected" button, "Deselect" button
- "Process Selected" queues pipeline tasks for all selected episodes
- `pb-20` padding on list to prevent content behind bar

### Empty State

- Archive icon + "No episodes found"
- Context-aware message: "Try adjusting your filters" or "Add a podcast to get started"

---

## 7. Digests List

**Route**: `/digests`

### Layout

```
+--------------------------------------------------+
| Digests                              [+ New Digest]
| Generated summaries of your podcast episodes      |
+--------------------------------------------------+

+------------+  +------------+  +------------+  +------------+
| 8          |  | 6          |  | 1          |  | 1          |
| Total      |  | Completed  |  | Partial    |  | Failed     |
+------------+  +------------+  +------------+  +------------+

+--------------------------------------------------+
| [spinner] Processing episodes...                  |
| [========60%========                ]  3/5 done   |
|                                                   |
| Digest from Mar 14, 2026, 8:00 AM                |
| Covers: Mar 7 - Mar 14                           |
| [In Progress] 3/5 episodes                       |
|                                  [View] [Delete]  |
+--------------------------------------------------+

+--------------------------------------------------+
| Digest from Mar 7, 2026, 8:00 AM                 |
| Covers: Feb 28 - Mar 7                           |
| [Completed] 5/5 episodes  2m                     |
|                                  [View] [Delete]  |
+--------------------------------------------------+

+--------------------------------------------------+
| About Digests                                     |
| Completed: all episodes processed successfully    |
| Partial: some failed but content still available  |
| Failed: could not be generated                    |
+--------------------------------------------------+
```

### Stats Grid

- 4 stat cards: Total Digests, Completed (green), Partial (orange), Failed (red)
- 2 columns mobile, 4 columns tablet+

### Digest Cards

- **Active digests** (pending/in_progress): Blue border highlight, progress bar with spinner + "Processing episodes... X of Y completed"
- **All digests**: Created date as title (linked to detail), period covered, status badge, episode counts, success rate (if partial), processing time
- **Error message**: Red text, truncated to 1 line
- **Actions**: "View" button (primary) + "Delete" button (gray)
- **Delete confirmation**: Inline "Yes"/"No" buttons replace the Delete button

### Empty State

- Document icon + "No digests yet" + "Create your first digest to get started" + CTA button

### Info Box

- Gray background box explaining digest statuses (Completed, Partial, Failed)

### New Digest Button

- Top-right, primary color with plus icon
- Opens Create Digest Modal

### Create Digest Modal

```
+----------------------------------------------+
| Create New Digest                             |
| Generate a digest from your processed podcast |
| episodes                                      |
+----------------------------------------------+
|                                               |
| Time window (days)                            |
| [7                                         ]  |
| Include episodes from the last 7 days         |
|                                               |
| Maximum episodes                              |
| [10                                        ]  |
| Limit digest to 10 episodes                   |
|                                               |
| [x] Only include already-summarized episodes  |
| [ ] Exclude episodes already in a digest      |
|                                               |
| [Preview selection                          ] |
|                                               |
| +------------------------------------------+ |
| | Preview: 5 of 8 matching episodes        | |
| | - Episode A - Podcast X       [summarized]| |
| | - Episode B - Podcast Y       [summarized]| |
| | - Episode C - Podcast X       [cleaned]   | |
| +------------------------------------------+ |
|                                               |
+----------------------------------------------+
|                        [Cancel] [Create Digest]|
+----------------------------------------------+
```

- **Time window**: Number input (1-365 days)
- **Max episodes**: Number input (1-100)
- **Ready only checkbox**: Default checked (recommended)
- **Exclude digested checkbox**: Default unchecked
- **Preview button**: Fetches matching episodes without creating
- **Preview results**: Scrollable list (max-h 192px) showing episode titles, podcast names, state badges
- **Create button**: Disabled when preview shows 0 matches or during creation
- **Scrollable body**: `max-h-[90vh]` for tall content

---

## 8. Digest Detail

**Route**: `/digests/:digestId`

### Layout

```
Digests / Mar 14, 2026                             (breadcrumb)

+--------------------------------------------------+
| Digest from Monday, March 14, 2026    [Completed] |
| Covers: Mar 7, 2026 - Mar 14, 2026               |
|                                                   |
| Episodes: 5/5   Success rate: 100%   Time: 2m    |
|                                                   |
| --- (separator) ---                               |
| [Delete digest]                                   |
+--------------------------------------------------+

[Content]  [Episodes (5)]                  (tab bar)

+--------------------------------------------------+
|                                                   |
| # Morning Briefing - March 14, 2026              |
|                                                   |
| ## Key Highlights                                 |
| - Point 1 about episode A...                     |
| - Point 2 about episode B...                     |
|                                                   |
| ## Episode Summaries                              |
| ### Episode A - Podcast X                         |
| Summary content rendered as markdown...           |
|                                                   |
+--------------------------------------------------+
```

### Header Card

- Title: "Digest from [full date with time]"
- Period covered: short date range
- Status badge (top-right, colored pill)
- Stats row: Episodes (completed/total, with failed count in red if any), Success rate %, Processing time
- Error message (if failed): red alert box
- Delete action: text button, inline confirmation ("Delete this digest?" + "Yes, delete" / "Cancel")

### Tabs

- **Content tab**: Rendered markdown digest using `ReactMarkdown` with prose styling
  - "Digest content not available" with context if pending/failed
- **Episodes tab**: List of episode items included in the digest
  - Each item: 48x48 artwork + linked episode title + podcast name + state badge + pub date
  - Hover highlight on items

---

## 9. Failed Tasks

**Route**: `/failed`

### Layout

```
+--------------------------------------------------+
| Failed Tasks           [Select all] [Retry all]   |
| Tasks that failed and need manual intervention    |
+--------------------------------------------------+

+------------+  +------------+  +------------+  +------------+
| 8          |  | 5          |  | 3          |  | 2          |
| Total      |  | Transient  |  | Fatal      |  | Selected   |
+------------+  +------------+  +------------+  +------------+

+--------------------------------------------------+
| [x] | Episode Title                               |
|     | Podcast Name                                |
|     | [transcribe] [transient] Retries: 2/3       |
|     | > Show error          Expand                |
|     | Failed at Mar 14, 8:30 AM                   |
|     |                          [Retry] [Skip]     |
+--------------------------------------------------+

+--------------------------------------------------+
| About Failed Tasks                                |
| Transient errors: temporary, may succeed on retry |
| Fatal errors: permanent, need investigation       |
| Skip: marks resolved without processing          |
+--------------------------------------------------+
```

### Stats Grid

- 4 stat cards: Total Failed, Transient (yellow), Fatal (red), Selected (gray)

### Task Selection

- Checkbox per task on the left side
- "Select all" / "Deselect all" toggle in header
- "Retry all" or "Retry N selected" bulk action button

### DLQ Task Cards

- **Episode link**: Title links to Episode Detail
- **Podcast name**: Gray subtitle
- **Badges**: Stage badge (colored by pipeline stage) + error type badge (transient yellow / fatal red)
- **Retry count**: "Retries: 2/3"
- **Error toggle**: "Show error" / "Hide error" expandable with chevron icon
- **Expanded error**: Preformatted text block with word-wrap
- **"Expand" link**: Opens full-screen FailureDetailsModal
- **Timestamp**: "Failed at [date/time]"
- **Actions**: "Retry" button (blue primary) + "Skip" button (gray secondary)
- Both show loading state ("Retrying...", "Skipping...")

### Failure Details Modal

```
+----------------------------------------------+
| Failed: Episode Title                    [X]  |
+----------------------------------------------+
| Podcast: Podcast Name                         |
| Stage: transcribe                             |
| Type: transient                               |
| Failed at: Mar 14, 2026, 8:30 AM             |
| Retries: 2/3                                  |
|                                               |
| Error Message:                                |
| +------------------------------------------+ |
| | Full error message with stack trace...    | |
| +------------------------------------------+ |
|                                               |
| [View Episode]               [Retry]          |
+----------------------------------------------+
```

### Empty State

- Checkmark circle icon + "No failed tasks" + "All tasks are running smoothly"

### Info Box

- Explains transient vs fatal errors and the Skip action

---

## 10. Task Queue

**Route**: `/queue`

### Layout

```
+--------------------------------------------------+
| Task Queue                    [Worker: Running]   |
| Monitor background processing tasks              |
+--------------------------------------------------+

+------------+  +------------+  +------------+  +------------+
| 1          |  | 3          |  | 1          |  | 5          |
| Processing |  | Pending    |  | Retry Sched|  | Completed  |
| (blue)     |  |            |  | (yellow)   |  | (green)    |
+------------+  +------------+  +------------+  +------------+

[blue dot] Currently Processing
+--------------------------------------------------+
| Episode Title                                     |
| Podcast Name                                      |
| [transcribe]  Processing for 2m 30s               |
+--------------------------------------------------+

Pending (3)
+--------------------------------------------------+
| Episode Title                          [^] [X]    |
| Podcast Name                                      |
| [download]  In queue for 5m                       |
+--------------------------------------------------+

Retry Scheduled (1)
+--------------------------------------------------+
| Episode Title                                     |
| Podcast Name                                      |
| [transcribe] Retry #1   Retry in 3m 20s          |
+--------------------------------------------------+

Recently Completed (5)
+--------------------------------------------------+
| Episode Title                                     |
| Podcast Name                                      |
| [summarize]  Waited 30s  Processed in 1m 15s     |
+--------------------------------------------------+

+--------------------------------------------------+
| About the Task Queue                              |
| Tasks processed in priority order.                |
| [^] bump moves task to front of queue             |
| [X] cancel removes pending task                   |
| Retry scheduled tasks auto-retry after backoff    |
+--------------------------------------------------+
```

### Worker Status Badge

- Top-right: green "Worker: Running" or red "Worker: Stopped"

### Stats Grid

- 4 stat cards: Processing (blue), Pending, Retry Scheduled (yellow), Completed/recent (green)

### Task Sections

Displayed in priority order, each section only appears if tasks exist:

1. **Currently Processing**: Blue pulsing dot indicator, spinner, processing duration
2. **Pending**: With bump (up arrow) and cancel (X) icon buttons per task, queue wait time
3. **Retry Scheduled**: Countdown to next retry, retry count
4. **Recently Completed**: Wait time + processing time metrics

### Task Cards

- Episode title (links to Episode Detail)
- Podcast name
- Stage badge (colored)
- Duration info (context-dependent: processing time, queue time, retry time, wait+process time)
- Retry count if > 0

### Idle State

- Checkmark circle icon + "Queue is idle" + "No tasks are currently processing"
- Only shown when no processing, pending, or retry-scheduled tasks

### Action Buttons (Pending tasks only)

- **Bump** (up chevron icon): Moves task to front of queue
- **Cancel** (X icon): Removes task from queue
- Both disabled while any action is in progress

### Info Box

- Explains bump/cancel actions and retry scheduling behavior

---

## Cross-Cutting Concerns for UX Review

### Navigation & Wayfinding

- Breadcrumbs on detail pages (Podcast Detail, Episode Detail, Digest Detail)
- Active nav item highlighting in sidebar
- No breadcrumb on list pages (Dashboard, Podcasts, Episodes, Digests, Failed Tasks, Queue)

### Real-Time Updates

- All data auto-polls every 5 seconds
- SSE streaming for transcription progress
- Active tasks show live countdowns and progress
- No WebSocket connection - polling-based

### Error Handling

- API errors shown as centered red alert cards
- Toast notifications for action results (success/error/info)
- Inline error messages on action buttons (auto-dismiss after 5s)
- "Back to [parent]" links on error states

### Loading Patterns

- Skeleton loaders match the shape of actual content
- Full-page "Loading..." text for operational pages (Digests, Failed, Queue)
- Spinner indicators for infinite scroll loading

### Mobile Considerations

- Icon-only buttons on mobile (Follow, Unfollow)
- Compact summary bar replaces stat cards on mobile Dashboard
- Pipeline status hidden on mobile Dashboard
- Episode meta dots hidden on mobile ("." separators become line breaks)
- Breadcrumb segments truncated on mobile
- Cards have reduced padding on mobile (p-3 vs p-4)
- Filters wrap naturally on mobile (flex-wrap)

### Accessibility Notes (Current State)

- Native HTML5 audio player
- `aria-label` on some icon buttons
- No skip-to-content link
- No ARIA landmarks beyond native HTML
- Focus management on modal open (auto-focus on input)
- Escape key closes modals
- Keyboard navigation relies on native browser behavior
