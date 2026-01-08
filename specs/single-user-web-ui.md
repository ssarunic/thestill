# thestill.me - Single-User Web UI

> **Status**: Active Development
> **Created**: 2025-12-19
> **Last Updated**: 2025-12-19

## Overview

A clean, minimal web interface for a single user (no authentication required). This is a stepping stone toward the full multi-user app, focusing on visibility and control over the podcast processing pipeline.

---

## Dashboard Overview

A clean, minimal dashboard with:

- **Status cards** showing total podcasts tracked, episodes processed today, and processing queue status
- **Recent activity feed** displaying newly processed episodes with timestamps
- **System health indicators** (storage usage, API quota remaining, processing capacity)

---

## Podcast Management

### Add Feed Section

- Simple URL input and "Add Podcast" button
- Support for RSS, Apple Podcasts, YouTube URLs

### Podcast Grid/List

- Podcast artwork thumbnails
- Title, description, and feed status
- Episode count and last update time
- Quick actions (pause tracking, remove, manual refresh)

---

## Episode Browser

### Filterable Episode List

- Search by title, date, or podcast
- Episode cards displaying:
  - Title, duration, publish date
  - Processing status badges (queued, transcribing, summarizing, complete)
  - Quick preview of summary/quotes when processed
- Bulk actions for processing selected episodes

---

## Processing Queue

- Real-time queue viewer showing current jobs with progress bars
- Processing logs with expandable details for debugging
- Manual controls to pause/resume processing or prioritize episodes

---

## Content Viewer

Episode detail pages with:

- Full transcript with timestamp navigation
- Generated summary with key points
- Notable quotes section with context
- Audio player integrated with transcript scrolling
- Export options (markdown, PDF, JSON)

---

## Settings Panel

- Configuration management for Whisper model selection, LLM parameters
- Storage management with cleanup tools and usage metrics
- API key management and quota monitoring

---

## Visual Design Language

### Color Palette

| Purpose | Color | Hex |
|---------|-------|-----|
| Primary | Deep audio waveform blue | `#1a365d` |
| Secondary | Warm podcast orange | `#ed8936` |
| Background light | Clean gray | `#f7fafc` |
| Background medium | | `#e2e8f0` |
| Text | Dark gray | `#4a5568` |
| Success | Green | `#48bb78` |
| Processing | Amber | `#ed8936` |
| Error | Red | `#e53e3e` |

### Typography

| Element | Font | Weight | Size |
|---------|------|--------|------|
| H1 | Inter/Roboto | Medium | 32px |
| H2 | Inter/Roboto | Medium | 24px |
| Body | Inter/Roboto | Regular | 16px |
| Small | Inter/Roboto | Regular | 14px |
| Transcript | Georgia/Charter | Regular | 16px |
| Monospace | JetBrains Mono | Regular | 14px |

### Layout & Spacing

- **Grid system**: 12-column responsive grid with generous whitespace
- **Cards**: Subtle shadows with rounded corners (8px radius)
- **Spacing**: Consistent 8px baseline grid (8px, 16px, 24px, 32px)
- **Containers**: Max-width content areas (1200px) preventing excessive line lengths

### Interactive Elements

- **Buttons**: Rounded rectangles with hover states and loading spinners
- **Progress bars**: Smooth animations for transcription/processing status
- **Form inputs**: Clean borders with focus states and validation feedback
- **Navigation**: Breadcrumbs and clear hierarchical paths

### Audio-Specific UI

- Waveform visualizations for episode previews
- Timestamp scrubbers integrated with transcript text
- Audio player controls with familiar podcast app styling
- Speaker indicators with color-coded dialogue in transcripts

### Content Presentation

- **Transcript formatting**: Clear paragraph breaks, speaker labels, timestamps in margins
- **Quote highlights**: Emphasized blocks with attribution and context
- **Summary sections**: Collapsible outline format with bullet points
- **Search results**: Highlighted matches with snippet context

### Responsive Behavior

| Breakpoint | Layout |
|------------|--------|
| Mobile (<768px) | Touch-friendly controls, simplified navigation, stacked cards |
| Tablet (768-1024px) | Sidebar navigation with main content area |
| Desktop (>1024px) | Full dashboard with multiple panels and detailed views |

---

## Technical Architecture

### Frontend Stack

- **React** with TypeScript
- **Vite** for build tooling
- **TailwindCSS** for styling (matches design system)
- **React Query** for server state management
- **React Router** for navigation

### Backend Integration

Extends existing FastAPI server (`thestill/web/`):

- No authentication required (single user)
- Reuses existing services (`PodcastService`, `StatsService`)
- New API endpoints under `/api/` prefix

### File Structure

```text
thestill/web/
├── app.py                         # FastAPI app (existing)
├── dependencies.py                # AppState (existing)
├── routes/
│   ├── health.py                  # Existing
│   ├── webhooks.py                # Existing
│   ├── api_podcasts.py            # NEW: Podcast CRUD
│   ├── api_episodes.py            # NEW: Episode content
│   └── api_stats.py               # NEW: Dashboard stats
├── frontend/                      # NEW: React SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Podcasts.tsx
│   │   │   ├── Episodes.tsx
│   │   │   ├── EpisodeDetail.tsx
│   │   │   ├── Queue.tsx
│   │   │   └── Settings.tsx
│   │   ├── components/
│   │   │   ├── Layout/
│   │   │   ├── Cards/
│   │   │   ├── AudioPlayer/
│   │   │   └── TranscriptViewer/
│   │   ├── api/
│   │   ├── hooks/
│   │   └── styles/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── index.html
└── static/                        # Built assets
```

---

## API Endpoints

### Dashboard (`/api/dashboard`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats` | Dashboard statistics (counts, storage, health) |
| GET | `/activity` | Recent activity feed |

### Podcasts (`/api/podcasts`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List all podcasts |
| POST | `/` | Add new podcast `{url}` |
| GET | `/{id}` | Get podcast details |
| DELETE | `/{id}` | Remove podcast |
| POST | `/{id}/refresh` | Trigger feed refresh |

### Episodes (`/api/episodes`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | List episodes (filterable) |
| GET | `/{id}` | Get episode details |
| GET | `/{id}/transcript` | Get transcript content |
| GET | `/{id}/summary` | Get summary content |
| POST | `/{id}/process` | Trigger processing |

### Queue (`/api/queue`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Current queue status |
| GET | `/jobs` | List jobs with status |
| POST | `/{id}/cancel` | Cancel job |
| POST | `/{id}/retry` | Retry failed job |

### Settings (`/api/settings`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Get current config |
| PATCH | `/` | Update config |
| GET | `/storage` | Storage usage details |
| POST | `/cleanup` | Trigger cleanup |

---

## Implementation Phases

### Phase 1: API Layer (1-2 days)

- [ ] Create API routes for podcasts, episodes, dashboard
- [ ] Expose existing service methods via REST
- [ ] Add CORS configuration for development

### Phase 2: Frontend Setup (1 day)

- [ ] Initialize Vite + React + TypeScript
- [ ] Set up TailwindCSS with design tokens
- [ ] Create basic layout components
- [ ] Configure API client

### Phase 3: Dashboard (1-2 days)

- [ ] Status cards with live data
- [ ] Recent activity feed
- [ ] System health indicators

### Phase 4: Podcast Management (1-2 days)

- [ ] Add podcast form
- [ ] Podcast list/grid view
- [ ] Quick actions (refresh, remove)

### Phase 5: Episode Browser (2 days)

- [ ] Episode list with filters
- [ ] Search functionality
- [ ] Status badges
- [ ] Bulk processing

### Phase 6: Content Viewer (2-3 days)

- [ ] Transcript viewer with timestamps
- [ ] Summary display
- [ ] Audio player integration
- [ ] Export functionality

### Phase 7: Queue & Settings (1-2 days)

- [ ] Queue viewer
- [ ] Settings panel
- [ ] Storage management

---

## Design Decisions

1. **Real-time updates**: Polling every 5 seconds (simple, good enough)
2. **Processing triggers**: Manual only (full control, no surprise costs)
3. **Audio player**: Link to external podcast URL (no storage overhead)
4. **MVP scope**: Read-only first - view existing data, no add/remove/process actions initially

## MVP Scope (Phase 1)

**Included:**

- Dashboard with stats and recent activity
- Podcast list (read-only)
- Episode browser with filters
- Transcript viewer with timestamps
- Summary viewer
- Audio player (external URL)

**Deferred to Phase 2:**

- Add/remove podcasts
- Manual processing triggers
- Queue management
- Settings panel
- Export functionality
