# Connecting Claude Desktop to thestill (spec #28 §1.11)

The `thestill-mcp` console script (defined in `pyproject.toml`) speaks
the Model Context Protocol over stdio. Once the server runs in your
shell, Claude Desktop can route tool calls — `find_mentions`,
`list_quotes_by`, `get_episode_clip`, `get_entity`, `list_episodes_by_entity`,
plus the existing podcast/digest tools — through it.

## Prerequisites

- The repo installed in editable mode with the `entities` extra:

  ```bash
  pip install -e ".[entities]"
  ```

- A populated SQLite database at the path your `.env` points at
  (`STORAGE_PATH=./data` by default).
- (Optional but expected) ReFinED's Wikidata index downloaded — runs
  automatically on the first `thestill resolve-entities` call.

## Manifest snippet

Append the following to your Claude Desktop config (typically
`~/Library/Application Support/Claude/claude_desktop_config.json` on
macOS):

```jsonc
{
  "mcpServers": {
    "thestill": {
      "command": "/absolute/path/to/thestill/venv/bin/thestill-mcp",
      "args": [],
      "env": {
        // Optional — overrides the auto-detected .env. Useful if
        // Claude Desktop launches the server outside the repo cwd.
        "THESTILL_ENV_FILE": "/absolute/path/to/thestill/.env"
      }
    }
  }
}
```

Restart Claude Desktop after editing. The hammer icon in the chat
input should list `thestill` among the available MCP servers; click
to expand and see the entity-layer tools.

## Sanity check (without Claude)

If you want to verify the server boots before pointing Claude at it:

```bash
# stdio: echoes a JSON-RPC tools/list and prints the tool catalog
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | thestill-mcp
```

Expected output: a JSON-RPC envelope listing
`add_podcast`, `find_mentions`, `list_quotes_by`,
`get_episode_clip`, `get_entity`, `list_episodes_by_entity`, plus the
podcast/digest tools.

## Tool guide for the LLM

Each tool's input schema is published via `tools/list` so Claude
auto-discovers them, but here is a quick reference:

| Tool | Use it for |
|------|-----------|
| `find_mentions(entity, ...)` | "What episodes mention X?" |
| `list_quotes_by(speaker, topic?, ...)` | "What has Galloway said about data centres?" |
| `get_episode_clip(episode_id, start_ms, ...)` | Turn an `(episode, t)` pointer into a quotable, playable citation |
| `get_entity(id_or_name)` | Entity pages — record + counts + cooccurrences + recent quotes |
| `list_episodes_by_entity(has_entity[])` | "Episodes containing both X and Y" |

Every result row carries an `episode_id`, `start_ms`, `quote`, and a
`thestill://episode/<id>?t=<sec>` deeplink so Claude can compose
narrative answers from cited clips.

## The harness reference questions (spec #28 §1.12)

Claude is meant to answer the 10 questions in
[`tests/fixtures/eval/harness_reference_questions.json`](../tests/fixtures/eval/harness_reference_questions.json)
against this MCP alpha with **no fabrication**: every quoted phrase
in the answer must trace back to a `quote` field returned by a
tool call in the same turn.

Run the offline grader (it walks each question through the tool
dispatcher and reports per-question PASS/FAIL/SKIPPED):

```bash
thestill harness-eval
```

The grader is the cheaper analogue of the live Claude Desktop run.
It can't verify "no fabrication" (Claude isn't in the loop) but it
does verify that the underlying data + tool surface support each
question — the floor for a good Claude run.
