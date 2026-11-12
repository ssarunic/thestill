# Use thestill in Claude

Thestill connects to Claude as a connector. Once connected, Claude can search the podcast transcripts and summaries on thestill, read the episodes in your inbox, and, if you allow it, follow shows and import episodes for you.

The connector address is:

```
{{connector_address}}
```

## What you can ask

- **"What is new in my thestill inbox? Pick the three episodes most worth reading."** Claude lists your inbox and reads the summaries.
- **"Who has talked about Nvidia this month, and what did they say?"** Claude finds episodes that mention a person, company or topic and returns the quotes with timestamps.
- **"What has this guest said about housing? Quote them."** Claude lists quotes attributed to a speaker across episodes.
- **"Find the moment they argued about interest rates so I can listen to it."** Claude searches the transcripts and returns a link that plays the audio from that point.
- **"Summarise the latest episode of this show in five bullets."** Claude reads the episode's summary or its full transcript.
- **"Follow this show for me"** or **"Import this episode: (link)"**. Claude adds the show to your follows, or brings one episode into your inbox. Only with the "Follows & imports" permission.

Answers link back to the episode page on thestill.

## Set it up

You need a thestill account and a Claude plan that supports custom connectors.

1. In Claude, open **Customize → Connectors**, choose **+ Add**, then **Add custom connector**.
2. Paste the connector address shown above and continue.
3. Claude sends you to thestill. Sign in, choose what Claude may do, and approve.

Anthropic's own guide has the current steps and plan rules: [Get started with custom connectors](https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp).

Other apps that support remote MCP servers with OAuth sign-in can use the same address.

## What you allow

When you connect, thestill asks which permissions to give:

| Permission | What it allows |
|---|---|
| Read (always on) | See the shows you follow, your inbox, episodes, summaries, transcripts and search. |
| Follows & imports (your choice) | Follow and unfollow shows, import an episode, and send an episode to your inbox. |

A connected app sees what you can see when signed in to thestill, and nothing more.

## The tools

Read tools. These never change anything:

| Tool | What it does |
|---|---|
| `list_podcasts` | Lists the shows you follow. |
| `list_episodes` | Lists a show's episodes. |
| `list_inbox` | Lists the episodes in your inbox, newest first. It does not mark them read. |
| `get_summary` | Returns an episode's summary. |
| `get_transcript` | Returns an episode's transcript, with speakers and timestamps. |
| `get_episode_clip` | Returns a quoted, playable reference for one moment in an episode. |
| `search_corpus` | Searches across transcripts. |
| `find_mentions` | Finds where a person, company, product or topic is mentioned. |
| `list_quotes_by` | Lists quotes attributed to a speaker. |
| `get_entity` | Describes a person, company, product or topic and where it appears. |
| `list_episodes_by_entity` | Lists the episodes that mention a person, company, product or topic. |
| `get_status` | Returns counts of shows and episodes in the library. |

Write tools. These need the "Follows & imports" permission:

| Tool | What it does |
|---|---|
| `add_podcast` | Follows a show, by RSS, Apple Podcasts or YouTube link. |
| `remove_podcast` | Unfollows a show. Claude asks you to confirm first. |
| `import_episode` | Imports one episode by link and sends it to your inbox. |
| `send_to_inbox` | Sends an episode already on thestill to your inbox. |

## Disconnect

Open [Settings](/settings) in thestill and find "Claude and other AI apps". Each connected app is listed there with a **Disconnect** button. Disconnecting stops that app's access at once. You can also remove the connector in Claude.

## Privacy

- Thestill does not see your conversation: not your question and not Claude's answer. It sees only the tool calls Claude makes, such as a search query or an episode request.
- Those tool calls, including the search text, are written to thestill's server logs, which are kept for {{log_retention}}.
- Unless you turn usage analytics off in Settings, thestill also records which tool was called and whether it worked. That record never includes what was searched for.
- What Claude reads from thestill is then handled under your agreement with Anthropic.

The full policy is in the [Privacy Policy](/privacy).

## Help

Summaries, transcripts and quotes are produced by a language model and speech recognition, so they can be wrong. Check a quote against the audio before relying on it.

Questions or problems: [{{contact_email}}](mailto:{{contact_email}}).
