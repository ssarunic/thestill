# MCP Server Implementation Plan for thestill.ai

## Executive Summary

Build an MCP (Model Context Protocol) server to expose thestill's podcast transcription functionality to any MCP-compatible client (Claude Desktop, IDEs, etc.). This implementation introduces a **service layer** to decouple business logic from presentation layers, enabling future expansion to web interfaces.

**Version 1.0 Scope**: Read-only operations, podcast/episode management, transcript retrieval. Transcription triggers excluded (future enhancement).

---

## Architecture Overview

### Current Architecture
```
CLI (cli.py)
    ↓
Core Components (feed_manager, transcriber, etc.)
    ↓
Data Storage (feeds.json, audio/, transcripts/, summaries/)
```

### Target Architecture
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   CLI       │     │  MCP Server │     │  Web (Future)│
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           ↓
                  ┌────────────────┐
                  │ Service Layer  │
                  └────────┬───────┘
                           ↓
              ┌────────────────────────┐
              │   Core Components      │
              │  (feed_manager, etc.)  │
              └────────┬───────────────┘
                       ↓
                  Data Storage
```

---

## 1. Service Layer Design

### Location
`thestill/services/`

### Components

#### 1.1 `podcast_service.py`
Manages podcast and episode CRUD operations with intelligent ID resolution.

```python
class PodcastService:
    """
    Handles podcast management with flexible ID resolution:
    - Podcast ID: RSS URL, integer index (1, 2, 3...)
    - Episode ID: GUID, integer index (1=latest, 2=second latest), 'latest', date string
    """

    def __init__(self, storage_path: str):
        self.feed_manager = PodcastFeedManager(storage_path)

    # Core Operations
    def add_podcast(self, url: str) -> Podcast
    def remove_podcast(self, url: str) -> bool
    def list_podcasts() -> List[PodcastWithIndex]

    # Flexible Lookups
    def get_podcast(self, podcast_id: str | int) -> Optional[Podcast]
        # Accepts: RSS URL, integer index (1-based)

    def get_episode(self, podcast_id: str | int, episode_id: str | int) -> Optional[Episode]
        # podcast_id: RSS URL or index
        # episode_id: GUID, integer index (1=latest), 'latest', 'YYYY-MM-DD'

    def list_episodes(
        self,
        podcast_id: str | int,
        limit: int = 10,
        since_hours: Optional[int] = None
    ) -> List[EpisodeWithIndex]
        # Returns episodes with index numbers (1=latest)
        # Optionally filter by publish date (last N hours)
```

**Models**:
```python
class PodcastWithIndex(Podcast):
    index: int  # 1-based index for human reference

class EpisodeWithIndex(Episode):
    index: int  # 1-based index within podcast (1=latest)
    podcast_index: int  # Parent podcast index
```

#### 1.2 `stats_service.py`
System statistics and status information.

```python
class StatsService:
    def __init__(self, storage_path: str):
        self.feed_manager = PodcastFeedManager(storage_path)
        self.storage_path = Path(storage_path)

    def get_stats() -> SystemStats

class SystemStats(BaseModel):
    podcasts_tracked: int
    episodes_total: int
    episodes_processed: int
    episodes_unprocessed: int
    transcripts_available: int
    audio_files_count: int
    storage_path: str
    last_updated: datetime
```

---

## 2. Resource ID Strategy

### Philosophy
**Human-first, LLM-friendly identifiers** that balance readability with precision.

### Podcast Identification

| Method | Format | Example | Use Case |
|--------|--------|---------|----------|
| **Index** | Integer (1-based) | `1`, `2`, `3` | Quick reference, "podcast 1" |
| **RSS URL** | Full URL | `https://feeds.megaphone.fm/...` | Canonical, precise |

**Resolution Logic**:
- If input is numeric → treat as index
- Otherwise → treat as RSS URL
- `list_podcasts` returns both for reference

### Episode Identification

| Method | Format | Example | Use Case |
|--------|--------|---------|----------|
| **Index** | Integer (1-based) | `1`, `2`, `3` | Latest episodes, "episode 2" |
| **Special** | String keyword | `latest` | Current episode |
| **Date** | ISO date | `2025-01-15` | Find by publish date |
| **GUID** | Original GUID | `abc123-def456` | Precise lookup |

**Resolution Logic**:
1. If `"latest"` → most recent episode
2. If numeric → Nth latest episode (1=newest)
3. If date format (YYYY-MM-DD) → match by publish date
4. Otherwise → treat as GUID

**Combined Reference**: `{podcast_id}/{episode_id}`
- `1/latest` → Podcast #1, latest episode
- `2/3` → Podcast #2, 3rd latest episode
- `1/2025-01-15` → Podcast #1, episode from Jan 15

### MCP Resource URIs

```
thestill://podcasts/{podcast_id}
    Examples:
    - thestill://podcasts/1
    - thestill://podcasts/https%3A%2F%2Ffeeds.example.com%2Frss

thestill://podcasts/{podcast_id}/episodes/{episode_id}
    Examples:
    - thestill://podcasts/1/episodes/latest
    - thestill://podcasts/1/episodes/3
    - thestill://podcasts/2/episodes/2025-01-15
    - thestill://podcasts/1/episodes/abc123-guid

thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript
    Examples:
    - thestill://podcasts/1/episodes/latest/transcript
    - thestill://podcasts/1/episodes/2/transcript
```

---

## 3. MCP Server Implementation

### 3.1 Transport: STDIO (v1.0)

**Why STDIO First**:
- Standard MCP protocol for local tools
- Works with Claude Desktop, Cursor, etc.
- No authentication needed (local trust)
- No HTTP infrastructure required

**Migration Path to HTTP**:
- Business logic unchanged (MCP SDK abstracts transport)
- Simple config change to enable SSE server
- Estimated effort: 1-2 hours

**Logging Strategy**:
- All application logs → `stderr`
- MCP protocol → `stdin/stdout`
- Use Python's `logging` module configured to stderr handler

### 3.2 File Structure

```
thestill/mcp/
├── __init__.py           # Package init
├── server.py             # Main MCP server setup
├── resources.py          # Resource handlers
├── tools.py              # Tool handlers
├── models.py             # MCP-specific request/response models
└── utils.py              # Helper functions (ID resolution, etc.)
```

### 3.3 Resources (Read-only Data)

MCP resources provide access to data that clients can read.

#### Resource: `thestill://podcasts/{podcast_id}`

**Purpose**: Get podcast metadata

**Input**: Podcast ID (index or URL)

**Output**: JSON
```json
{
  "index": 1,
  "title": "The Rest is Politics",
  "description": "...",
  "rss_url": "https://feeds.example.com/rss",
  "last_processed": "2025-01-15T10:30:00Z",
  "episodes_count": 150,
  "episodes_processed": 45
}
```

#### Resource: `thestill://podcasts/{podcast_id}/episodes/{episode_id}`

**Purpose**: Get episode metadata

**Input**: Podcast ID + Episode ID

**Output**: JSON
```json
{
  "podcast_index": 1,
  "episode_index": 3,
  "title": "Nigel Farage and the Future of Reform",
  "description": "...",
  "pub_date": "2025-01-14T06:00:00Z",
  "duration": "45:30",
  "guid": "abc123-def456",
  "processed": true,
  "audio_url": "https://traffic.megaphone.fm/...",
  "transcript_available": true,
  "summary_available": true
}
```

#### Resource: `thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript`

**Purpose**: Get cleaned transcript content

**Input**: Podcast ID + Episode ID

**Output**:
- **If processed**: Cleaned Markdown transcript content
- **If not processed**: Plain text `"N/A - Episode not yet processed"`

**Example**:
```markdown
# The Rest is Politics - Nigel Farage and Reform

**Alastair Campbell**: Welcome back to the Rest is Politics...

**Rory Stewart**: Thanks Alastair. Today we're discussing...

[... full cleaned transcript ...]
```

### 3.4 Tools (Actions)

MCP tools allow clients to perform actions.

#### Tool: `add_podcast`

**Purpose**: Add a new podcast to tracking

**Input**:
```json
{
  "url": "https://podcasts.apple.com/podcast/id123456"
}
```

**Output**:
```json
{
  "success": true,
  "message": "Podcast added: The Rest is Politics",
  "podcast_index": 3
}
```

**Supports**: RSS URLs, Apple Podcast URLs, YouTube channels/playlists

#### Tool: `remove_podcast`

**Purpose**: Remove a podcast from tracking

**Input**:
```json
{
  "podcast_id": "1"
}
```
or
```json
{
  "podcast_id": "https://feeds.example.com/rss"
}
```

**Output**:
```json
{
  "success": true,
  "message": "Podcast removed"
}
```

#### Tool: `list_podcasts`

**Purpose**: List all tracked podcasts with indices

**Input**: None

**Output**:
```json
{
  "podcasts": [
    {
      "index": 1,
      "title": "The Rest is Politics",
      "rss_url": "https://feeds.example.com/rss",
      "episodes_count": 150,
      "episodes_processed": 45,
      "last_processed": "2025-01-15T10:30:00Z"
    },
    {
      "index": 2,
      "title": "Lex Fridman Podcast",
      "rss_url": "https://lexfridman.com/feed/podcast/",
      "episodes_count": 400,
      "episodes_processed": 12,
      "last_processed": "2025-01-14T08:00:00Z"
    }
  ]
}
```

#### Tool: `list_episodes`

**Purpose**: List episodes for a podcast with flexible filtering

**Input**:
```json
{
  "podcast_id": "1",
  "limit": 10,
  "since_hours": 24
}
```

**Parameters**:
- `podcast_id` (required): Podcast index or URL
- `limit` (optional, default=10): Max episodes to return
- `since_hours` (optional): Only episodes published in last N hours

**Output**:
```json
{
  "podcast_title": "The Rest is Politics",
  "podcast_index": 1,
  "episodes": [
    {
      "index": 1,
      "title": "Nigel Farage and Reform",
      "pub_date": "2025-01-15T06:00:00Z",
      "duration": "45:30",
      "processed": true,
      "transcript_available": true
    },
    {
      "index": 2,
      "title": "Gaza Ceasefire Talks",
      "pub_date": "2025-01-13T06:00:00Z",
      "duration": "50:15",
      "processed": false,
      "transcript_available": false
    }
  ]
}
```

#### Tool: `get_status`

**Purpose**: Get system-wide statistics

**Input**: None

**Output**:
```json
{
  "podcasts_tracked": 3,
  "episodes_total": 550,
  "episodes_processed": 87,
  "episodes_unprocessed": 463,
  "transcripts_available": 87,
  "audio_files_count": 95,
  "storage_path": "/Users/user/data",
  "last_updated": "2025-01-15T14:30:00Z"
}
```

---

## 4. Implementation Steps

### Phase 1: Foundation (Logging & Services)

**Step 1.1**: Fix Logging System
- Configure Python `logging` module to use stderr
- Update all `print()` and `click.echo()` statements to use logger
- Test CLI still works correctly
- **Estimated time**: 2-3 hours

**Step 1.2**: Create Service Layer
- Create `thestill/services/` directory
- Implement `podcast_service.py` with ID resolution logic
- Implement `stats_service.py`
- Add comprehensive docstrings
- **Estimated time**: 4-5 hours

**Step 1.3**: Refactor CLI to Use Services
- Update `cli.py` to import and use services
- Keep all CLI presentation logic (click.echo, formatting)
- Test all CLI commands work identically
- **Estimated time**: 2-3 hours

### Phase 2: MCP Server

**Step 2.1**: MCP Server Setup
- Add `mcp` dependency to `pyproject.toml`
- Create `thestill/mcp/` directory structure
- Implement `server.py` with STDIO transport
- Basic health check
- **Estimated time**: 2-3 hours

**Step 2.2**: Implement Resources
- Create `resources.py` with handlers
- Implement `thestill://podcasts/{id}` resource (podcast metadata)
- Implement `thestill://podcasts/{id}/episodes/{id}` resource (episode metadata)
- Implement `thestill://podcasts/{id}/episodes/{id}/transcript` resource (transcript)
- Implement `thestill://podcasts/{id}/episodes/{id}/audio` resource (audio reference)
- **Estimated time**: 3-4 hours

**Step 2.3**: Implement Tools
- Create `tools.py` with handlers
- Implement `add_podcast` tool
- Implement `remove_podcast` tool
- Implement `list_podcasts` tool
- Implement `list_episodes` tool
- Implement `get_status` tool
- **Estimated time**: 4-5 hours

**Step 2.4**: MCP Entry Point
- Add CLI entry point: `thestill-mcp` script
- Configuration loading for MCP server
- Graceful shutdown handling
- **Estimated time**: 1-2 hours

### Phase 3: Testing & Documentation

**Step 3.1**: Integration Testing
- Test with MCP Inspector tool
- Test with Claude Desktop
- Validate all resource URIs work
- Validate all tools work
- **Estimated time**: 3-4 hours

**Step 3.2**: Documentation
- Update README.md with MCP setup instructions
- Create MCP_USAGE.md with examples
- Document resource URI patterns
- Document tool usage patterns
- **Estimated time**: 2-3 hours

**Total Estimated Time**: 24-32 hours

---

## 5. Future Enhancements (Post v1.0)

### v1.1: Transcription Triggers
- Tool: `transcribe_episode(podcast_id, episode_id)`
- Async job tracking with status polling
- Progress notifications via MCP

### v1.2: HTTP Transport
- Switch to SSE server for remote access
- Add authentication (API keys or OAuth)
- CORS configuration for web clients

### v1.3: Advanced Search
- Natural language episode search
- Full-text transcript search
- Speaker-based filtering

### v1.4: Batch Operations
- Tool: `transcribe_all_new_episodes()`
- Bulk export to various formats
- Scheduled processing

---

## 6. Example Usage Scenarios

### Scenario 1: Add and Explore Podcast
```
User: "Add The Rest is Politics podcast"
Claude → calls add_podcast("https://podcasts.apple.com/...")
Claude → "Added podcast #1: The Rest is Politics"

User: "What episodes are available?"
Claude → calls list_episodes(podcast_id="1", limit=5)
Claude → Shows list with indices

User: "Get me the transcript of episode 2"
Claude → reads thestill://podcasts/1/episodes/2/transcript
Claude → Displays cleaned markdown
```

### Scenario 2: Recent Episodes Across All Podcasts
```
User: "What episodes were published in the last 24 hours?"
Claude → calls list_podcasts()
Claude → calls list_episodes() for each with since_hours=24
Claude → Summarizes findings
```

### Scenario 3: System Overview
```
User: "How many podcasts do I have tracked?"
Claude → calls get_status()
Claude → "You're tracking 3 podcasts with 550 total episodes.
          87 have been processed and transcripts are available."
```

---

## 7. Configuration

### Environment Variables (.env)
Shared between CLI and MCP server:

```bash
# Existing variables (unchanged)
STORAGE_PATH=./data
OPENAI_API_KEY=sk-...
WHISPER_MODEL=base
# ... etc

# MCP-specific (optional)
MCP_TRANSPORT=stdio  # or 'http' in future
MCP_LOG_LEVEL=INFO
```

### MCP Server Configuration
Uses same `thestill/utils/config.py` configuration loader.

---

## 8. Dependencies

### New Dependencies
```toml
# Add to pyproject.toml
dependencies = [
    # ... existing dependencies
    "mcp>=1.0.0",  # Official MCP Python SDK
]

[project.scripts]
thestill = "thestill.cli:main"
thestill-mcp = "thestill.mcp.server:main"  # New MCP entry point
```

---

## 9. Success Criteria

### v1.0 Complete When:
- ✅ Service layer extracts all business logic from CLI
- ✅ CLI refactored to use services (no regressions)
- ✅ MCP server runs via `thestill-mcp` command
- ✅ All 5 tools work correctly
- ✅ All 3 resource types accessible
- ✅ ID resolution works for all supported formats
- ✅ Tested with Claude Desktop
- ✅ Documentation complete

### Quality Gates:
- No breaking changes to existing CLI commands
- All podcast/episode lookups < 100ms
- Logging doesn't interfere with STDIO transport
- Error messages are clear and actionable

---

## 10. Risk Mitigation

### Risk: Breaking CLI During Refactor
**Mitigation**:
- Implement services first
- Test services independently
- Refactor CLI incrementally (one command at a time)
- Keep git commits small and testable

### Risk: ID Resolution Ambiguity
**Mitigation**:
- Clear precedence rules (numeric → index, else → URL/GUID)
- Document edge cases
- Provide examples in tool descriptions

### Risk: STDIO Logging Interference
**Mitigation**:
- Fix logging FIRST (Phase 1.1)
- Test CLI extensively before MCP work
- Use Python logging framework exclusively

### Risk: Poor LLM Experience with IDs
**Mitigation**:
- Always return indexed lists with metadata
- Include human-readable titles in all responses
- Support multiple ID formats for flexibility

---

## End of Plan

This plan provides a complete roadmap for implementing a robust, human-friendly MCP server for thestill.ai that can be extended to HTTP and additional features in future versions.
