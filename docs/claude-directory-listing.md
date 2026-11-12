# Claude directory listing kit

Everything the submission portal at
[claude.ai/directory/manage](https://claude.ai/directory/manage) asks for,
in the order it asks (spec #109 Phase 3). Paste from here; fill the two
items marked **To do**.

Requirements were read from Anthropic's
[submission guide](https://claude.com/docs/connectors/building/submission)
and [review criteria](https://claude.com/docs/connectors/building/review-criteria)
on 2026-11-12. Check them again before submitting.

## Before you open the portal

- [ ] The release with tool titles and hints is deployed (PR #510).
- [ ] The privacy policy no longer shows its Draft banner.
- [ ] A reviewer test account exists (see step 8).
- [ ] The connector check passes against prod (see below).
- [ ] Every tool was also run once from a Claude conversation.

## The connector check

`scripts/check_connector.py` connects as a real MCP client, confirms every
tool has a title and hints, and calls each tool with valid arguments.

```bash
# The personal connector URL from Settings → "Claude and other AI apps" →
# Advanced. It is a credential: keep it out of shell history.
read -rs THESTILL_MCP_URL && export THESTILL_MCP_URL

# Read tools only. Changes nothing.
./venv/bin/python scripts/check_connector.py

# Also the write tools. Run this as the test account, not your own:
# it leaves one episode in the inbox, follows and unfollows one show,
# and imports one episode (which runs the pipeline).
./venv/bin/python scripts/check_connector.py --write \
  --follow-url '<RSS, Apple Podcasts or YouTube link of a show>' \
  --import-url '<episode link>'
```

It exits 1 if any tool fails. The personal URL needs the "Follows &
imports" scope for `--write`. The check goes through the personal URL,
not OAuth, so it does not test the sign-in and consent screens: do that
once by hand in Claude.

## 1. Connection

- Server URL: `https://thestill.me/mcp`
- Users connect to different URLs: no.

## 2. Tools

Synced from the server. Expect 12 read tools and 4 write tools for a
non-admin account. Nothing should be flagged for a missing title or
annotation.

## 3. Listing

- **Name** (100 max): Thestill
- **One-liner** (200 max): Search and read podcast transcripts and
  summaries, find who said what, and follow shows from Claude.
- **Description** (2,000 max):

  ```text
  Thestill transcribes and summarises podcasts. This connector lets Claude work with that library and with your own Thestill inbox.

  Read tools: search across transcripts, find mentions of a person, company or topic, list quotes by a speaker, open an episode's summary or full transcript with speaker names and timestamps, get a quoted reference to one moment in an episode, and list the shows you follow and the episodes in your inbox.

  Write tools: follow or unfollow a show, import a single episode by link, and send an episode to your inbox. Unfollowing is marked as destructive, so Claude asks before doing it.

  Results carry the episode, the speaker and the timestamp, with a link that opens the episode page at that moment.

  You need a Thestill account (Google sign-in). When you connect, you choose whether Claude may only read or may also manage your follows and imports. You can disconnect at any time in Thestill's Settings.

  Summaries, transcripts and quotes are produced by a language model and speech recognition, so they can be wrong.
  ```

- **Categories** (1 to 5): pick from the portal's list. Likely fits:
  media or entertainment, research, productivity.
- **Documentation URL:** `https://thestill.me/claude`
- **Privacy policy URL:** `https://thestill.me/privacy`
- **Support contact:** `support@thestill.me`
- **Icon:** `thestill/web/frontend/public/favicon.svg` (vector), or
  `apple-touch-icon.png` in the same folder (180 × 180 PNG).
- **URL slug** (permanent once published): `thestill`

## 4. Use cases

- **Primary use cases:**
  - "What have people said about AI agents this month? Quote them with
    timestamps."
  - "What is new in my inbox? Pick the three episodes most worth reading."
  - "Find the moment they argued about interest rates so I can listen to
    it."
  - "Summarise the latest episode of this show in five bullets."
  - "Follow this show for me."
- **What users need first:** a Thestill account, created by signing in
  with Google at `https://thestill.me`.
- **Reads or writes:** both.

## 5. Company

- **Name and website:** from `public/legal/operator.json`
  (`operator_name`), `https://thestill.me`
- **Primary contact:** `support@thestill.me`

## 6. Authentication

- **Authentication requirement:** required. Every tool needs sign-in.
- **Method:** OAuth 2.0. Thestill is its own authorization server; the
  client registers itself (client ID metadata document, with dynamic
  registration as the fallback), so there is no client ID or secret to
  enter.
- **Scopes:** `read` (always), `follows` (the user's choice at consent),
  `offline_access`.

## 7. Data handling

- **Whose API:** our own. The connector reads Thestill's own database.
- **Personal health data:** no.
- **Sponsored content:** no.

## 8. Test & launch

**To do:** a test account that is already populated (a few follows, some
read and unread inbox episodes), and how a reviewer signs in to it.
Thestill only offers Google sign-in, so this is either a Google account
you own and use for nothing else, or a reviewer sign-in that does not
depend on Google.

Reviewer steps to paste once the account exists:

```text
1. In Claude, add a custom connector with the URL https://thestill.me/mcp.
2. Claude opens Thestill's sign-in page. Sign in with the test account below.
3. On the consent screen, leave "Read" on and tick "Follows & imports", then approve.
4. Try: "What have people on Thestill said about AI agents this month? Quote them with timestamps."
5. Try: "What is in my Thestill inbox?"
6. Try: "Send the first of those search results to my inbox."
Setup guide: https://thestill.me/claude
```

Confirm on this step that you ran every tool yourself: the connector
check above, plus one pass in Claude.

## 9. Compliance

The seven acknowledgments, with the facts behind each:

| Acknowledgment | Thestill |
|---|---|
| Directory guidelines | Read and accepted by the submitter. |
| First-party API usage | The server calls only Thestill's own data; the domain matches the service. |
| Financial transactions | None. No tool moves money. |
| AI media generation | None through the connector. Tools return text and links to the publisher's original audio. |
| Prompt injection | Tool descriptions say what a tool does and nothing else. Transcripts are third-party speech, returned as data (spec #96). |
| Conversation data collection | None. The server sees tool calls and their arguments, which are written to server logs kept for the period in `operator.json` (`log_retention`). It never sees the question or the answer. |
| Public documentation | `https://thestill.me/claude` |

## After submitting

Status and reviewer feedback appear in the portal. Escalations:
`mcp-review@anthropic.com`.

**To do after listing:** tick the "Claude directory" box in
[spec #109](../specs/109-mcp-oauth.md) Phase 3.
