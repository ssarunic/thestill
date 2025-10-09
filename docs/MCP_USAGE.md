# MCP Server Usage Guide

This guide explains how to use the thestill.ai MCP server with Claude Desktop and other MCP-compatible clients.

## Installation

1. **Install thestill with MCP support:**

```bash
cd /path/to/thestill
pip install -e .
```

This installs both the CLI tool (`thestill`) and the MCP server (`thestill-mcp`).

2. **Verify installation:**

```bash
thestill-mcp --help  # Should show help (once implemented)
```

## Configuration

The MCP server uses the same `.env` configuration as the CLI tool. Ensure your `.env` file is set up:

```bash
# Required
STORAGE_PATH=./data

# Optional (for transcription features)
OPENAI_API_KEY=sk-...
WHISPER_MODEL=base

# MCP-specific (optional)
MCP_LOG_LEVEL=INFO
```

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
  "guid": "abc123-def456",
  "processed": true,
  "audio_url": "https://...",
  "transcript_available": true,
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
- `"N/A - Episode not yet processed"` (if not processed)


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
      "processed": true,
      "transcript_available": true
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

- Episodes need to be transcribed first via CLI
- Use CLI: `thestill transcribe /path/to/audio.mp3`
- Then use CLI: `thestill process` to clean transcripts

---

## Tips for Using with Claude

1. **Use natural language:**
   - ✅ "Show me the latest episode from podcast 1"
   - ✅ "What's new in the last 24 hours?"
   - ✅ "Add The Daily podcast"

2. **Reference by index for speed:**
   - ✅ "Transcript of episode 1.2" (podcast 1, episode 2)
   - ✅ "List episodes from podcast 3"

3. **Use dates when relevant:**
   - ✅ "Show me the episode from January 15"
   - ✅ "Episodes from last week"

4. **Combine tools creatively:**
   - ✅ "Find mentions of 'AI' across all transcripts"
   - ✅ "Compare viewpoints between podcast 1 and 2"
   - ✅ "Summarize this week's episodes"

---

## Next Steps

- **Add more podcasts:** Use the `add_podcast` tool
- **Process episodes:** Use CLI to transcribe: `thestill transcribe audio.mp3`
- **Clean transcripts:** Use CLI to process: `thestill process`
- **Explore transcripts:** Ask Claude to analyze, summarize, or compare!

For more information, see:
- [MCP Implementation Plan](MCP_IMPLEMENTATION_PLAN.md)
- [Main README](../README.md)
- [Transcript Cleaning Guide](TRANSCRIPT_CLEANING.md)
