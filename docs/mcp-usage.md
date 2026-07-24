# MCP Server Usage Guide

This guide explains how to use the Thestill MCP server with Claude Desktop, ChatGPT Desktop, and other MCP-compatible clients.

## Supported Clients

- **Claude Desktop** (Anthropic) - Full MCP support
- **ChatGPT Desktop** (OpenAI) - MCP support via plugin system
- **Any MCP-compatible client** - Standard MCP protocol

## Installation

1. **Install thestill with MCP support:**

```bash
cd /path/to/thestill
pip install -e ".[entities]"
```

This installs both the CLI tool (`thestill`) and the MCP server (`thestill-mcp`). The `entities` extra is required: the MCP server unconditionally initializes the embedding/search backend at startup, and it also powers the entity and corpus search tools (`find_mentions`, `search_corpus`, etc.).

1. **Verify installation:**

```bash
# The console script should be on your PATH
command -v thestill-mcp

# Optional: verify the server answers a tools/list over stdio
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | thestill-mcp
```

Note: `thestill-mcp` takes no arguments — it speaks MCP over stdio and blocks waiting for a client, so there is no `--help` flag.

## Configuration

The MCP server uses the same `.env` configuration as the CLI tool. Ensure your `.env` file is set up:

```bash
# Required
STORAGE_PATH=./data

# Optional (for transcription features)
OPENAI_API_KEY=sk-...
WHISPER_MODEL=base
```

The MCP server uses the shared logging configuration (`LOG_LEVEL`, `LOG_FORMAT`) — see [logging-configuration.md](logging-configuration.md).

## Setting Up with Claude Desktop

Claude Desktop is the easiest way to use the MCP server.

### Step 1: Locate Claude Desktop Configuration

The configuration file location depends on your OS:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

### Step 2: Add thestill MCP Server

Edit the configuration file and add the thestill MCP server:

```json
{
  "mcpServers": {
    "thestill": {
      "command": "thestill-mcp",
      "args": [],
      "env": {
        "STORAGE_PATH": "/path/to/your/data"
      }
    }
  }
}
```

**Note**: Replace `/path/to/your/data` with the actual path to your thestill data directory.

### Step 3: Restart Claude Desktop

Close and reopen Claude Desktop for the changes to take effect.

### Step 4: Verify Connection

In Claude Desktop, you should see a small indicator showing the MCP server is connected. You can now use natural language to interact with your podcast library!

---

## Setting Up with ChatGPT Desktop

ChatGPT Desktop supports MCP servers through its plugin system.

### Locate Configuration File

The configuration file location depends on your OS:

- **macOS**: `~/Library/Application Support/ChatGPT/chatgpt_config.json`
- **Windows**: `%APPDATA%\ChatGPT\chatgpt_config.json`

### Add Server Configuration

Edit the configuration file and add the thestill MCP server:

```json
{
  "mcpServers": {
    "thestill": {
      "command": "thestill-mcp",
      "args": [],
      "env": {
        "STORAGE_PATH": "/path/to/your/data"
      }
    }
  }
}
```

**Note**: Replace `/path/to/your/data` with the actual path to your thestill data directory.

### Restart and Verify

Close and reopen ChatGPT Desktop for the changes to take effect. You should see the thestill tools available when you start a new conversation. You can now use natural language to interact with your podcast library!

---

## Available Resources

MCP resources provide read-only access to data. Claude can read these automatically.

### Podcast Metadata

**URI Format**: `thestill://podcasts/{podcast_id}`

**Podcast ID can be:**

- Integer index: `thestill://podcasts/1`, `thestill://podcasts/2`, etc.
- RSS URL (URL-encoded): `thestill://podcasts/https%3A%2F%2Ffeeds.example.com%2Frss`

**Example Usage in Claude:**

```
User: "Tell me about podcast 1"
Claude: [reads thestill://podcasts/1]
Claude: "Podcast #1 is 'The Rest is Politics' with 150 episodes, 45 of which have been processed..."
```

**Returns**: JSON with podcast metadata

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

### Episode Metadata

**URI Format**: `thestill://podcasts/{podcast_id}/episodes/{episode_id}`

**Episode ID can be:**

- Integer index: `1` (latest), `2` (second latest), etc.
- Keyword: `latest`
- Date: `2025-01-15`
- GUID: exact episode identifier

**Example Usage in Claude:**

```
User: "What's the latest episode from podcast 1?"
Claude: [reads thestill://podcasts/1/episodes/latest]
Claude: "The latest episode is 'Nigel Farage and Reform' published on Jan 15, 2025..."
```

**Returns**: JSON with episode metadata

```json
{
  "podcast_index": 1,
  "episode_index": 1,
  "title": "Nigel Farage and Reform",
  "description": "...",
  "pub_date": "2025-01-15T06:00:00Z",
  "duration": "45:30",
  "external_id": "abc123-def456",
  "state": "summarized",
  "audio_url": "https://...",
  "transcript_available": true,
  "clean_transcript_available": true,
  "summary_available": true
}
```

### Episode Transcript

**URI Format**: `thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript`

**Example Usage in Claude:**

```
User: "Show me the transcript of episode 1.2"
Claude: [reads thestill://podcasts/1/episodes/2/transcript]
Claude: [displays the full cleaned Markdown transcript]
```

**Returns**:

- Cleaned Markdown transcript (if processed)
- `"N/A - No transcript available"` (if not processed)

### Episode Audio Reference

**URI Format**: `thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio`

**Example Usage in Claude:**

```
User: "Where can I find the audio for episode 1.1?"
Claude: [reads thestill://podcasts/1/episodes/1/audio]
Claude: "The audio is available at [URL] and is 45 minutes long..."
```

**Returns**: JSON with audio metadata

```json
{
  "audio_url": "https://traffic.megaphone.fm/...",
  "duration": "45:30",
  "title": "Episode Title",
  "local_file": false,
  "format": "audio/mpeg"
}
```

### Episode Summary

**URI Format**: `thestill://podcasts/{podcast_id}/episodes/{episode_id}/summary`

**Example Usage in Claude:**

```
User: "Show me the summary of episode 1.1"
Claude: [reads thestill://podcasts/1/episodes/1/summary]
Claude: [displays the comprehensive summary with executive summary, quotes, and analysis]
```

**Returns**:

- Comprehensive Markdown summary (if summarized)
- `"N/A - Episode not yet summarized"` (if not summarized)

---

## Available Tools

MCP tools allow Claude to perform actions. Claude will call these when appropriate.

### 1. `add_podcast`

Add a new podcast to tracking.

**Parameters:**

- `url` (string, required): RSS feed URL, Apple Podcast URL, or YouTube channel/playlist URL

**Example Usage:**

```
User: "Add The Rest is Politics podcast"
Claude: [calls add_podcast with URL]
Claude: "I've added podcast #3: The Rest is Politics"
```

**Response:**

```json
{
  "success": true,
  "message": "Podcast added: The Rest is Politics",
  "podcast_index": 3,
  "podcast": {
    "title": "The Rest is Politics",
    "description": "...",
    "rss_url": "https://..."
  }
}
```

### 2. `remove_podcast`

Remove a podcast from tracking.

**Parameters:**

- `podcast_id` (string, required): Podcast index (1, 2, 3...) or RSS URL

**Example Usage:**

```
User: "Remove podcast 2"
Claude: [calls remove_podcast with podcast_id="2"]
Claude: "Podcast removed successfully"
```

**Response:**

```json
{
  "success": true,
  "message": "Podcast removed successfully"
}
```

### 3. `list_podcasts`

List all tracked podcasts.

**Parameters:** None

**Example Usage:**

```
User: "What podcasts am I tracking?"
Claude: [calls list_podcasts]
Claude: "You're tracking 3 podcasts:
1. The Rest is Politics (45/150 episodes processed)
2. Lex Fridman Podcast (12/400 episodes processed)
3. ..."
```

**Response:**

```json
{
  "podcasts": [
    {
      "index": 1,
      "title": "The Rest is Politics",
      "rss_url": "https://...",
      "episodes_count": 150,
      "episodes_processed": 45,
      "last_processed": "2025-01-15T10:30:00Z"
    }
  ]
}
```

### 4. `list_episodes`

List episodes for a specific podcast.

**Parameters:**

- `podcast_id` (string, required): Podcast index or RSS URL
- `limit` (integer, optional, default=10): Maximum episodes to return
- `since_hours` (integer, optional): Only episodes from last N hours

**Example Usage:**

```
User: "What episodes were published in the last 24 hours?"
Claude: [calls list_episodes for each podcast with since_hours=24]
Claude: "In the last 24 hours, there were 2 new episodes:
- The Rest is Politics: 'Nigel Farage and Reform'
- Lex Fridman: 'Elon Musk Interview'"
```

**Response:**

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
      "state": "summarized",
      "transcript_available": true,
      "summary_available": true
    }
  ]
}
```

### 5. `get_status`

Get system-wide statistics.

**Parameters:** None

**Example Usage:**

```
User: "How many podcasts do I have?"
Claude: [calls get_status]
Claude: "You're tracking 3 podcasts with a total of 550 episodes.
87 have been processed and transcripts are available."
```

**Response:**

```json
{
  "podcasts_tracked": 3,
  "episodes_total": 550,
  "episodes_processed": 87,
  "episodes_unprocessed": 463,
  "transcripts_available": 87,
  "audio_files_count": 95,
  "storage_path": "/path/to/data",
  "last_updated": "2025-01-15T14:30:00Z"
}
```

### 6. `get_transcript`

Get the cleaned transcript for a specific episode.

**Parameters:**

- `podcast_id` (string, required): Podcast index (1, 2, 3...) or RSS URL
- `episode_id` (string, required): Episode index (1=latest), 'latest', date (YYYY-MM-DD), or GUID

**Example Usage:**

```
User: "Show me the transcript of the latest episode from podcast 1"
Claude: [calls get_transcript with podcast_id="1", episode_id="latest"]
Claude: [Displays the full cleaned Markdown transcript]
```

**Response:** Returns the cleaned Markdown transcript content directly, or an error message if not available.

---

## Pipeline Tools

The following tools allow you to process episodes through the transcription pipeline. The pipeline has 6 steps that must be run in order:

1. **refresh_feeds** - Discover new episodes from RSS feeds
2. **download_episodes** - Download audio files
3. **downsample_audio** - Convert to 16kHz WAV format
4. **transcribe_episodes** - Create raw transcripts
5. **clean_transcripts** - Clean with LLM for readability
6. **summarize_episodes** - Create comprehensive summaries with analysis

Alternatively, use **process_episode** to run steps 1-5 for a single episode, then use **summarize_episodes** to create the summary.

### 7. `refresh_feeds`

Refresh podcast feeds to discover new episodes. This is step 1 of the pipeline.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to refresh only that podcast
- `max_episodes` (integer, optional): Maximum episodes to discover per podcast

**Example Usage:**

```
User: "Check for new podcast episodes"
Claude: [calls refresh_feeds]
Claude: "Discovered 5 new episodes across your podcasts:
- The Rest is Politics: 2 new episodes
- Lex Fridman: 1 new episode
- Huberman Lab: 2 new episodes

Next step: Run download_episodes to download the audio."
```

**Response:**

```json
{
  "success": true,
  "message": "Discovered 5 new episode(s)",
  "total_episodes": 5,
  "episodes": [
    {"podcast_title": "The Rest is Politics", "episode_title": "...", "pub_date": "..."}
  ],
  "next_step": "Run download_episodes to download the audio files"
}
```

### 8. `download_episodes`

Download audio files for discovered episodes. This is step 2 of the pipeline.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to download only from that podcast
- `max_episodes` (integer, optional, default=5): Maximum episodes to download

**Example Usage:**

```
User: "Download the new episodes"
Claude: [calls download_episodes]
Claude: "Downloaded 3 episodes successfully. Next step: downsample the audio."
```

**Response:**

```json
{
  "success": true,
  "message": "Downloaded 3 episode(s)",
  "downloaded": [
    {"podcast": "The Rest is Politics", "episode": "Episode Title"}
  ],
  "next_step": "Run downsample_audio to prepare for transcription"
}
```

### 9. `downsample_audio`

Downsample downloaded audio to 16kHz WAV format for transcription. This is step 3 of the pipeline.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to downsample only from that podcast
- `max_episodes` (integer, optional, default=5): Maximum episodes to downsample

**Example Usage:**

```
User: "Prepare the audio for transcription"
Claude: [calls downsample_audio]
Claude: "Downsampled 3 episodes. Ready for transcription."
```

**Response:**

```json
{
  "success": true,
  "message": "Downsampled 3 episode(s)",
  "downsampled": [
    {"podcast": "The Rest is Politics", "episode": "Episode Title"}
  ],
  "next_step": "Run transcribe_episodes to create transcripts"
}
```

### 10. `transcribe_episodes`

Transcribe downsampled audio to JSON transcripts. This is step 4 of the pipeline.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to transcribe only from that podcast
- `max_episodes` (integer, optional, default=1): Maximum episodes to transcribe (low default due to processing time)

**Example Usage:**

```
User: "Transcribe the prepared episodes"
Claude: [calls transcribe_episodes]
Claude: "Transcribed 1 episode. The raw transcript is ready for cleaning."
```

**Response:**

```json
{
  "success": true,
  "message": "Transcribed 1 episode(s)",
  "transcribed": [
    {"podcast": "The Rest is Politics", "episode": "Episode Title"}
  ],
  "next_step": "Run clean_transcripts for better readability"
}
```

### 11. `clean_transcripts`

Clean raw transcripts with LLM processing for better readability. This is step 5 (final) of the pipeline.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to clean only from that podcast
- `max_episodes` (integer, optional, default=1): Maximum episodes to clean (low default due to LLM costs)

**Example Usage:**

```
User: "Clean up the transcripts"
Claude: [calls clean_transcripts]
Claude: "Cleaned 1 transcript with 45 corrections. Applied speaker identification."
```

**Response:**

```json
{
  "success": true,
  "message": "Cleaned 1 transcript(s)",
  "cleaned": [
    {"podcast": "The Rest is Politics", "episode": "Episode Title", "corrections": 45, "speakers": 2}
  ],
  "complete": "Transcripts are now ready! Use get_transcript to read them."
}
```

### 12. `process_episode`

Run the full processing pipeline for a specific episode. Convenient for processing a single episode end-to-end.

**Parameters:**

- `podcast_id` (string, required): Podcast index (1, 2, 3...) or RSS URL
- `episode_id` (string, required): Episode index (1=latest), 'latest', date (YYYY-MM-DD), or GUID

**Example Usage:**

```
User: "Process the latest episode from podcast 1"
Claude: [calls process_episode with podcast_id="1", episode_id="latest"]
Claude: "Fully processed episode 'Nigel Farage and Reform':
- Downloaded audio
- Downsampled to 16kHz
- Transcribed with Whisper
- Cleaned with LLM

The transcript is now ready to read!"
```

**Response:**

```json
{
  "success": true,
  "message": "Episode processed: Nigel Farage and Reform",
  "podcast": "The Rest is Politics",
  "episode": "Nigel Farage and Reform",
  "steps_completed": ["download", "downsample", "transcribe", "clean"],
  "transcript_ready": true
}
```

### 13. `summarize_episodes`

Summarize cleaned transcripts with comprehensive analysis. This is step 6 of the pipeline. Produces executive summary, notable quotes, content angles, social snippets, and critical analysis.

**Parameters:**

- `podcast_id` (string, optional): Podcast index or RSS URL to summarize only from that podcast
- `max_episodes` (integer, optional, default=1): Maximum episodes to summarize (low default due to LLM costs)

**Example Usage:**

```
User: "Summarize the latest cleaned transcript"
Claude: [calls summarize_episodes]
Claude: "Summarized 1 episode. The summary includes an executive summary, notable quotes, and content angles."
```

**Response:**

```json
{
  "success": true,
  "message": "Summarized 1 episode(s)",
  "summarized": [
    {"podcast": "The Rest is Politics", "episode": "Episode Title"}
  ],
  "complete": "Summaries are now ready! Use get_summary to read them."
}
```

### 14. `get_summary`

Get the comprehensive summary for a specific episode.

**Parameters:**

- `podcast_id` (string, required): Podcast index (1, 2, 3...) or RSS URL
- `episode_id` (string, required): Episode index (1=latest, 2=second latest, etc.), 'latest', date (YYYY-MM-DD), or GUID

**Example Usage:**

```
User: "Show me the summary of the latest episode from podcast 1"
Claude: [calls get_summary with podcast_id="1", episode_id="latest"]
Claude: [Displays the comprehensive summary with executive summary, quotes, and analysis]
```

**Response:** Returns the summary Markdown content directly, or an error message if not available.

---

## Entity & Search Tools

The following tools query the entity layer (resolved mentions of people, companies, products, and topics) and the search index. They require installing with the `entities` extra (`pip install -e ".[entities]"`) and a corpus that has been through entity resolution and indexing.

Most of these tools return **citation-shaped rows**: each row carries `episode_id`, `podcast_title`, `episode_title`, `published_at`, `start_ms`, `end_ms`, `speaker`, `quote`, a `thestill://episode/<id>?t=<sec>` deeplink, and a `web_url` — so Claude can compose narrative answers from cited, playable clips.

### 15. `find_mentions`

Find every resolved mention of an entity (person, company, product, or topic) across the corpus.

**Parameters:**

- `entity` (string, required): Entity name — canonical name, alias, or id like `person:elon-musk`
- `entity_type` (string, optional): One of `person`, `company`, `product`, `topic` — disambiguation hint when multiple entities share a name
- `podcast_id` (string, optional): Restrict to one podcast (UUID or slug)
- `date_from` (string, optional): ISO-8601 lower bound on episode pub_date (inclusive)
- `date_to` (string, optional): ISO-8601 upper bound on episode pub_date (inclusive)
- `role` (string, optional): One of `host`, `guest`, `mentioned`, `self` — filter by how the entity was named
- `limit` (integer, optional, default=50): Maximum rows to return

**Example Usage:**

```
User: "What episodes mention Elon Musk?"
Claude: [calls find_mentions with entity="Elon Musk"]
Claude: "Elon Musk is mentioned in 12 clips across 5 episodes..."
```

**Response:**

```json
{
  "success": true,
  "matched_entity": {
    "id": "person:elon-musk",
    "type": "person",
    "canonical_name": "Elon Musk",
    "wikidata_qid": "Q317521",
    "aliases": ["Musk"],
    "description": "..."
  },
  "results": [
    {
      "episode_id": "...",
      "podcast_title": "The Rest is Politics",
      "episode_title": "...",
      "published_at": "2025-01-15T06:00:00Z",
      "start_ms": 754000,
      "end_ms": 761000,
      "speaker": "Rory Stewart",
      "quote": "...",
      "score": 1.0,
      "match_type": "entity",
      "deeplink": "thestill://episode/<id>?t=754",
      "web_url": "/episodes/<id>?t=754"
    }
  ]
}
```

### 16. `list_quotes_by`

List resolved mentions where a specific speaker said something, optionally filtered by topic. Speaker matching is case-insensitive substring.

**Parameters:**

- `speaker` (string, required): Speaker name (matches the diarised label)
- `topic` (string, optional): Topic name or entity id — restricts to episodes where the topic was also mentioned
- `podcast_id` (string, optional): Restrict to one podcast
- `date_from` (string, optional): ISO-8601 lower bound on pub_date
- `date_to` (string, optional): ISO-8601 upper bound on pub_date
- `limit` (integer, optional, default=50): Maximum rows to return

**Example Usage:**

```
User: "What has Scott Galloway said about data centres?"
Claude: [calls list_quotes_by with speaker="Scott Galloway", topic="data centres"]
Claude: "Here are Scott Galloway's quotes on data centres, with citations..."
```

**Response:**

```json
{
  "success": true,
  "speaker": "Scott Galloway",
  "topic_entity_id": "topic:data-centres",
  "results": [
    { "...": "citation-shaped rows, as in find_mentions" }
  ]
}
```

### 17. `get_episode_clip`

Return one citation-shaped row for a specific clip in an episode. Turns an `(episode_id, start_ms)` pointer into a playable, quoted reference.

**Parameters:**

- `episode_id` (string, required): Episode UUID
- `start_ms` (integer, required): Position to anchor the clip at (milliseconds)
- `end_ms` (integer, optional): Explicit end (otherwise the segment's end_ms)
- `plus_minus_sec` (integer, optional): Symmetric window around `start_ms` in seconds — widens the resulting clip

**Example Usage:**

```
User: "Give me a playable citation for that quote"
Claude: [calls get_episode_clip with episode_id="...", start_ms=754000]
Claude: "Here's the clip: 'quote...' — thestill://episode/<id>?t=754"
```

**Response:**

```json
{
  "success": true,
  "result": { "...": "one citation-shaped row, as in find_mentions" }
}
```

### 18. `get_entity`

Return an entity record plus its mention count, top co-occurring entities, and recent mentions. Useful for entity pages and to disambiguate before calling `find_mentions`.

**Parameters:**

- `id_or_name` (string, required): Canonical id (`person:elon-musk`), canonical name (`Elon Musk`), or alias (`Musk`) — resolved in that order
- `entity_type` (string, optional): One of `person`, `company`, `product`, `topic` — disambiguation hint

**Example Usage:**

```
User: "Who is Sam Altman in my corpus?"
Claude: [calls get_entity with id_or_name="Sam Altman"]
Claude: "Sam Altman has 34 mentions, often co-occurring with OpenAI and Elon Musk..."
```

**Response:**

```json
{
  "success": true,
  "result": {
    "entity": {
      "id": "person:sam-altman",
      "type": "person",
      "canonical_name": "Sam Altman",
      "wikidata_qid": "...",
      "aliases": ["Altman"],
      "description": "..."
    },
    "mention_count": 34,
    "cooccurring": [
      {"entity": {"id": "company:openai", "...": "..."}, "episode_count": 8, "last_seen_at": "..."}
    ],
    "recent_mentions": [
      { "...": "citation-shaped rows, as in find_mentions" }
    ]
  }
}
```

### 19. `list_episodes_by_entity`

List episodes that contain at least one mention of every entity in `has_entity` (set intersection, AND-semantics).

**Parameters:**

- `has_entity` (array of strings, required): Entity ids or names — all must appear in the episode
- `podcast_id` (string, optional): Restrict to one podcast
- `date_from` (string, optional): ISO-8601 lower bound on pub_date
- `date_to` (string, optional): ISO-8601 upper bound on pub_date
- `limit` (integer, optional, default=50): Maximum rows to return

**Example Usage:**

```
User: "Which episodes feature both Scott Galloway and Andrew Yang?"
Claude: [calls list_episodes_by_entity with has_entity=["Scott Galloway", "Andrew Yang"]]
Claude: "Both appear together in 3 episodes..."
```

**Response:**

```json
{
  "success": true,
  "matched_entity_ids": ["person:scott-galloway", "person:andrew-yang"],
  "results": [
    {
      "episode_id": "...",
      "episode_title": "...",
      "published_at": "2025-01-15T06:00:00Z",
      "podcast_id": "...",
      "podcast_title": "The Rest is Politics",
      "podcast_slug": "the-rest-is-politics"
    }
  ]
}
```

### 20. `search_corpus`

Lexical, semantic, or hybrid search over the podcast corpus. Use this when the question isn't entity-scoped (e.g. "episodes about agentic engineering" rather than "what has Karpathy said" — for entity-scoped queries prefer `find_mentions`).

**Parameters:**

- `query` (string, required): Free-form search text; lexical mode supports BM25 syntax (`"quoted phrase"`, `-negation`)
- `mode` (string, optional, default=`hybrid`): One of:
  - `lexical` — BM25 keyword match; fast, exact terms
  - `semantic` — vector similarity; concept-level recall
  - `hybrid` — reciprocal rank fusion over both; best recall on novel phrasings
- `limit` (integer, optional, default=10): Maximum rows to return
- `filters` (object, optional): WHERE-clause filters pushed to SQL:
  - `podcast_id` (string)
  - `date_range` (object with ISO-8601 `from` / `to`)
  - `has_entity` (array of entity ids that must appear in the episode)

**Example Usage:**

```
User: "Find episodes about agentic engineering"
Claude: [calls search_corpus with query="agentic engineering"]
Claude: "3 episodes discuss agentic engineering, here are the key clips..."
```

**Response:**

```json
{
  "query": "agentic engineering",
  "mode": "hybrid",
  "results": [
    { "...": "citation-shaped rows, as in find_mentions" }
  ],
  "total": 3
}
```

---

## Usage Examples

### Example 1: Discover and Read Transcripts

```
User: "What podcasts do I have tracked?"

Claude: [calls list_podcasts]
I can see you're tracking 3 podcasts:
1. The Rest is Politics (45 episodes processed)
2. Lex Fridman Podcast (12 episodes processed)
3. Huberman Lab (30 episodes processed)

User: "Show me the latest episode from The Rest is Politics"

Claude: [reads thestill://podcasts/1/episodes/latest]
The latest episode is "Nigel Farage and the Future of Reform" published on January 15, 2025.
It's 45 minutes long and has been processed with a transcript available.

User: "Can you show me the transcript?"

Claude: [reads thestill://podcasts/1/episodes/latest/transcript]
[Displays full cleaned Markdown transcript]

Here's the transcript of the latest episode...
```

### Example 2: Find Recent Episodes

```
User: "Any new episodes in the last 24 hours?"

Claude: [calls list_episodes for each podcast with since_hours=24]
Yes! There are 2 new episodes:

1. The Rest is Politics (Podcast #1, Episode #1)
   - "Nigel Farage and Reform"
   - Published: Jan 15, 2025 at 6:00 AM
   - Processed: Yes, transcript available

2. Lex Fridman Podcast (Podcast #2, Episode #1)
   - "Elon Musk: War, AI, Aliens & Politics"
   - Published: Jan 15, 2025 at 2:00 AM
   - Processed: No

User: "Summarize the first one for me"

Claude: [reads thestill://podcasts/1/episodes/1/transcript]
[Reads and summarizes the transcript]
```

### Example 3: Add New Podcasts

```
User: "Add the All-In Podcast to my collection"

Claude: I'll search for the All-In Podcast URL and add it for you.
[calls add_podcast with URL]

✓ Successfully added podcast #4: All-In with Chamath, Jason, Sacks & Friedberg

User: "Show me the latest 5 episodes"

Claude: [calls list_episodes with podcast_id="4", limit=5]
Here are the latest 5 episodes from All-In:
1. "E167: Trump's victory, lessons learned, state of Silicon Valley" (Dec 20)
2. "E166: Bitcoin, nuclear energy, fission vs fusion" (Dec 13)
...
```

### Example 4: Analyze Multiple Transcripts

```
User: "Compare what Rory and Alastair said about Nigel Farage in their last 3 episodes"

Claude: [reads thestill://podcasts/1/episodes/1/transcript, thestill://podcasts/1/episodes/2/transcript, thestill://podcasts/1/episodes/3/transcript]
[Analyzes and compares mentions across episodes]

Based on the last 3 episodes of The Rest is Politics:

Episode 1 (Jan 15): Alastair was critical of Farage's Reform party strategy...
Episode 2 (Jan 13): Rory discussed Farage's influence on Conservative voters...
Episode 3 (Jan 11): Both hosts debated whether Farage poses a genuine threat...
```

### Example 5: Process New Episodes End-to-End

```
User: "Check for new episodes and process them"

Claude: [calls refresh_feeds]
Found 3 new episodes! Let me process the latest one for you.

[calls process_episode with podcast_id="1", episode_id="latest"]
Processing "Nigel Farage and Reform" from The Rest is Politics...

✓ Step 1: Downloaded audio file (45 MB)
✓ Step 2: Downsampled to 16kHz WAV
✓ Step 3: Transcribed with speaker diarization
✓ Step 4: Cleaned transcript with LLM

The episode is fully processed! Would you like me to show you the transcript or summarize it?

User: "Give me a quick summary"

Claude: [calls get_transcript with podcast_id="1", episode_id="latest"]
[Reads and summarizes the transcript]

Here's a summary of today's episode...
```

### Example 6: Batch Process Multiple Episodes

```
User: "Process all unprocessed episodes from The Rest is Politics"

Claude: Let me run through the pipeline for podcast 1.

[calls refresh_feeds with podcast_id="1"]
Discovered 5 new episodes.

[calls download_episodes with podcast_id="1", max_episodes=5]
Downloaded 5 episodes.

[calls downsample_audio with podcast_id="1", max_episodes=5]
Downsampled 5 episodes.

[calls transcribe_episodes with podcast_id="1", max_episodes=5]
Transcribed 5 episodes. (Note: This may take a while for long episodes)

[calls clean_transcripts with podcast_id="1", max_episodes=5]
Cleaned 5 transcripts.

All 5 episodes are now fully processed! You can read any of them using:
- "Show me the transcript for episode 1" (latest)
- "Show me episode 2's transcript" (second latest)
```

---

## Troubleshooting

### MCP Server Not Connecting

1. **Check logs:**
   - MCP server logs to stderr
   - Check Claude Desktop logs (Help → Show Logs)

2. **Verify configuration:**

   ```bash
   # Test the server manually
   thestill-mcp
   # Should start without errors
   ```

3. **Check paths:**
   - Ensure `STORAGE_PATH` in config points to valid directory
   - Ensure `thestill-mcp` is in your PATH

### "Podcast not found" Errors

- Verify podcast index with `list_podcasts` tool
- Indices are 1-based (first podcast is 1, not 0)
- Ensure podcast hasn't been removed

### "Episode not yet processed" for Transcripts

Episodes need to go through the full pipeline before transcripts are available. You can now do this directly via MCP tools:

1. **Quick method:** Use `process_episode` to run the full pipeline for a single episode
2. **Batch method:** Run each step separately:
   - `refresh_feeds` → discover new episodes
   - `download_episodes` → download audio
   - `downsample_audio` → prepare for transcription
   - `transcribe_episodes` → create raw transcript
   - `clean_transcripts` → clean with LLM

### Transcription Takes Too Long

- Transcription is CPU/GPU intensive and can take 10-30 minutes per hour of audio
- Use `max_episodes=1` (default) to process one episode at a time
- Consider using Google Cloud Speech-to-Text for faster cloud-based transcription

### LLM Cleaning Fails

- Ensure your LLM provider is configured in `.env` (OpenAI, Anthropic, Gemini, or Ollama)
- Check API key validity and rate limits
- For Ollama, ensure the service is running locally

---

## Tips for Using with Claude

1. **Use natural language:**
   - ✅ "Show me the latest episode from podcast 1"
   - ✅ "What's new in the last 24 hours?"
   - ✅ "Add The Daily podcast"
   - ✅ "Process the latest episode"

2. **Reference by index for speed:**
   - ✅ "Transcript of episode 1.2" (podcast 1, episode 2)
   - ✅ "List episodes from podcast 3"
   - ✅ "Process episode 1.1"

3. **Use dates when relevant:**
   - ✅ "Show me the episode from January 15"
   - ✅ "Episodes from last week"

4. **Combine tools creatively:**
   - ✅ "Find mentions of 'AI' across all transcripts"
   - ✅ "Compare viewpoints between podcast 1 and 2"
   - ✅ "Summarize this week's episodes"

5. **Pipeline shortcuts:**
   - ✅ "Check for new episodes and process the latest one"
   - ✅ "Process all new episodes from podcast 1"
   - ✅ "What's the status of my podcast processing?"

---

## Next Steps

- **Add more podcasts:** Use the `add_podcast` tool
- **Discover new episodes:** Use `refresh_feeds` to check for updates
- **Process episodes:** Use `process_episode` for single episodes or run pipeline steps individually
- **Read transcripts:** Use `get_transcript` to retrieve cleaned transcripts
- **Explore transcripts:** Ask Claude to analyze, summarize, or compare!

For more information, see:

- [Main README](../README.md)
- [Transcript Cleaning Guide](TRANSCRIPT_CLEANING.md)
