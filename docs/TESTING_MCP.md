# Testing the MCP Implementation

Quick guide for testing the newly implemented MCP server.

## Step 1: Install Dependencies

```bash
# Make sure you're in the thestill directory
cd /Users/sasasarunic/_Sources/thestill

# Install/reinstall with new dependencies
pip install -e .
```

This will install the `mcp` package and register the `thestill-mcp` command.

## Step 2: Verify Installation

```bash
# Verify thestill-mcp command is available
which thestill-mcp

# Check if CLI still works (no regressions)
thestill list
thestill status
```

## Step 3: Test MCP Server Locally

You can test the MCP server directly (it will use STDIO):

```bash
thestill-mcp
```

The server should start and wait for input. Press Ctrl+C to stop.

You should see logs like:
```
INFO - PodcastService initialized with storage: ./data
INFO - MCP server initialized successfully
INFO - Starting MCP server with STDIO transport
INFO - STDIO transport established
```

## Step 4: Configure Claude Desktop

1. **Find the config file:**
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - Linux: `~/.config/Claude/claude_desktop_config.json`

2. **Edit the config:**

```json
{
  "mcpServers": {
    "thestill": {
      "command": "/Users/sasasarunic/_Sources/thestill/venv/bin/thestill-mcp",
      "args": [],
      "env": {
        "STORAGE_PATH": "/Users/sasasarunic/_Sources/thestill/data"
      }
    }
  }
}
```

**Important**:
- Replace the `command` path with the actual path to `thestill-mcp` (use `which thestill-mcp` to find it)
- Replace `STORAGE_PATH` with your actual data directory path

3. **Restart Claude Desktop completely** (quit and reopen)

## Step 5: Test in Claude Desktop

Once Claude Desktop restarts, try these queries:

### Test 1: List Podcasts
```
What podcasts am I tracking?
```

Claude should call the `list_podcasts` tool and show your podcasts with their indices.

### Test 2: Get Status
```
How many episodes have I processed?
```

Claude should call `get_status` and provide statistics.

### Test 3: View Episode
```
Show me information about the latest episode from podcast 1
```

Claude should read `episode://1/latest` resource.

### Test 4: Read Transcript
```
Show me the transcript of episode 1 from podcast 1
```

Claude should read `transcript://1/1` resource.

### Test 5: Add Podcast (if desired)
```
Add the All-In podcast: https://feeds.megaphone.fm/all-in
```

Claude should call `add_podcast` tool.

## Troubleshooting

### MCP Server Not Connecting

1. **Check Claude Desktop logs:**
   - Look for errors about the MCP server
   - On macOS: Check Console app for "Claude" process

2. **Verify command path:**
   ```bash
   # Make sure this works
   /path/in/config/thestill-mcp
   ```

3. **Check environment:**
   ```bash
   # Test if storage path is valid
   ls $STORAGE_PATH
   ```

### "Podcast not found" Errors

- Run `thestill list` in terminal to verify podcasts exist
- Remember indices are 1-based (first podcast is 1, not 0)

### Import Errors

```bash
# If you see import errors, reinstall
pip install -e . --force-reinstall
```

### Logging Issues

If you see strange output, check that:
- All logging goes to stderr (not stdout)
- No print() statements remain in core code

## Verification Checklist

- [ ] `pip install -e .` succeeds
- [ ] `thestill-mcp` command exists
- [ ] `thestill list` still works (CLI not broken)
- [ ] `thestill status` shows enhanced statistics
- [ ] `thestill-mcp` starts without errors
- [ ] Claude Desktop shows MCP connection
- [ ] Can list podcasts in Claude
- [ ] Can read episode metadata
- [ ] Can read transcripts (if processed episodes exist)
- [ ] Can add new podcasts (optional)

## Next Steps After Testing

1. **Report Issues:**
   - Document any errors or unexpected behavior
   - Check logs for error messages

2. **Performance:**
   - Note how fast responses are
   - Check if there are any delays

3. **User Experience:**
   - Is the ID system intuitive?
   - Are error messages clear?
   - Does natural language work well?

4. **Edge Cases:**
   - Try invalid podcast IDs
   - Request transcripts for unprocessed episodes
   - Test with empty podcast library

## Success Criteria

Testing is successful when:
- ✅ MCP server connects to Claude Desktop
- ✅ All 5 tools work correctly
- ✅ All 3 resource types return data
- ✅ Natural language queries work smoothly
- ✅ Error messages are clear and helpful
- ✅ Performance is acceptable (<1s for most operations)

---

**Good luck with testing!** 🚀

See [docs/MCP_USAGE.md](docs/MCP_USAGE.md) for detailed usage examples.
