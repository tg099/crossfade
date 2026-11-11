# AI assistants and mood playlists

Crossfade exposes an authenticated **Streamable HTTP MCP server** at `/mcp` (also `/mcp/`). It runs
inside the existing application and Docker image, using the official MCP Python SDK. No separate
service, database or AI API key is needed. Your connected assistant supplies the model.

## Connect an assistant

1. Set `CROSSFADE_BASE_URL` to the address the assistant uses, e.g. `https://music.example.com`.
   The standard Docker Compose installation already sets this from your domain. Crossfade validates
   the MCP Host and Origin against this address; localhost is also allowed.
2. In Crossfade, open **Account → AI assistants → Access**
   (**Konto → KI-Assistenten → Zugänge** in German).
3. Choose **New access**, give it a name and an expiry (7, 30, 90 or 365 days). Reading is always
   allowed. **Sync** lets the assistant run fades; **Manage music and playlists** lets it create
   playlists, add songs, set up fades and save tags.
4. Create and copy the access key. It is shown only once; Crossfade stores its hash.
5. Add the displayed address to your assistant as a **Streamable HTTP** server, with this HTTP header:

   ```text
   Authorization: Bearer <your cf_mcp_… access key>
   ```

The assistant/client must support custom Bearer authentication headers. This implementation does
**not** provide an OAuth browser sign-in flow or a stdio transport. Clients that require OAuth
instead of personal tokens need an additional integration. Browser cookies and tokens in URLs
are never accepted by the MCP endpoint. Use HTTPS for a server outside localhost.

Each person creates their own connections. Revoke a connection from the same screen to stop new
requests immediately and cancel its pending proposals. Work already submitted to the background
worker may finish; `stop_library_job` and `stop_sync` can request cancellation. Password changes
and administrator password resets revoke the user's MCP keys. Admin-only instance functions and
service credentials are not exposed through MCP.

Set `CROSSFADE_MCP_ENABLED=0` to disable the endpoint globally. Existing connections can still be
viewed/revoked from the account screen. Restart after changing environment variables.

## What you can ask

- “Create a relaxed but not sad playlist from my library, around an hour, on Spotify.”
- “Sort my library into melancholic, happy, relaxed and energetic playlists.”
- “Find songs that fit my Late Night playlist and add the ones I choose.”
- “Which of my songs are missing on TIDAL or are not in any editable playlist yet?”
- “Show my broken fades and preview what the next sync would change.”
- “Find duplicates and suggest which version to keep.”
- “What disappeared from this playlist since Friday? Prepare to restore those songs.”

The assistant can build custom selections using genre, personal tags, language, year or estimated
energy when those annotations are known. It cannot infer listening frequency or play history:
Crossfade's catalog records collection membership, not listening history.

## How mood playlists work

The MCP server does **not** classify moods from title keywords or pretend to analyze audio. Your
assistant reads your scanned library, uses its knowledge or other information available to it, and
saves estimates with an explanation and confidence via `annotate_songs`. Unknown music and unknown
metadata should remain unclassified. No external metadata or audio-analysis service is automatically
contacted. Mood, genre, language, year and energy estimates are not verified provider metadata.

A song can have several moods, genres and personal tags. `list_labels` returns the labels already in
use (with song counts), so the assistant can reuse your vocabulary instead of inventing synonyms. The
assistant can either select songs explicitly for a free-form description or reuse an exact saved
mood/genre label. Different languages and synonyms are interpreted by the assistant; stored labels use
exact, case-insensitive matching. `clear_annotations` forgets the assistant's own estimates for given
songs; your corrections always stay.

`prepare_playlist` accepts:

- `account_id` and `name` for a new playlist, or `source_id` to append to an existing editable playlist;
- a free-form `mood`, an optional saved `genre`, and/or explicit `selections`;
- each selection's `song_id`, `reason`, `confidence` and optional `matched_moods`;
- `exclude_song_ids`, `max_tracks` (default 50, maximum 2,000), `duration_minutes` and
  `min_confidence` (default 0.7).

Explicit selections retain their order. A duration is an upper bound, not a promise to fill it exactly.
Songs with unknown duration are skipped when a time limit is set. Exclusions, low confidence,
limits and personal corrections appear in the preview. Automatic selection from saved labels
considers at most 2,000 candidates; assistants can page through the library and pass a curated selection.

The preview contains titles, artists, reasons, confidence, known duration and skipped songs.
Nothing is written to a music service until the action is applied. Existing playlist contents are
preserved when appending; already-present songs are not added again. Crossfade uses its existing
provider matching and background worker. Missing matches, failed transfers and partial completion
are returned in the final action result. The requested order and time limit concern the selected
library recordings; matched versions and a service's behavior can affect the final playlist.

### Correcting the assistant

Open **Account → AI assistants → Tags**. Tap a mood, genre or tag to see its songs, or search for a
song, and edit its moods, genres or tags. Separate several labels with commas. These corrections are
authoritative: assistants cannot overwrite them. For a free-form mood, the assistant must name the
fitting saved personal labels in each corrected song's `matched_moods`; a contradictory selection is
skipped. A personal empty mood list excludes the song from mood playlists. A correction made after a
proposal requires a fresh playlist preview. **Forget this** allows assistant classification again.

Annotations are per person, even for a song whose identity is shared with another user. They survive
song identity merges, with personal corrections taking precedence over assistant estimates.

## Previews, approval and progress

Every change is prepared first: a `prepare_*` tool validates it and returns an immutable preview.
A proposal is not an executed change.

1. The assistant prepares an action. If you already asked for the change it applies it right away
   with `apply_action`; otherwise it shows you the preview first.
2. **Only deletions wait for you in Crossfade.** That covers removing songs, removing duplicates,
   deleting or merging playlists, deleting a fade, resetting a collection exactly to an earlier state
   (`restore` in `exact` mode), carrying out held deletions (`prepare_sync` with `allow_deletions`),
   and switching a fade's deletion sync on or raising its deletion safety limit. Everything else —
   playlists, adding songs, creating and changing fades, pausing/resuming, syncing, matching songs,
   swapping versions, changing a collection's direction, restoring missing songs — the assistant
   carries out itself. New fades start with deletion sync off unless the assistant switches it on.
3. A deletion proposal returns an `approve_url`. Open it (or **Account → AI assistants → Actions**,
   or the card on the home page) to see the preview and **Allow deletion** or **Discard** it.
   If you set up notifications with the event *something needs my confirmation*, Crossfade also
   sends you a message with that link. MCP can never approve its own deletions.
4. The assistant polls `get_action` until the worker reports a final state and explains any failures.

Each proposal expires after 24 hours and can be cancelled. A token may execute only proposals made
by that connection and must have the required permission. Crossfade checks ownership and access again
at execution. If something changed in between so that a proposal would now delete (for example the
fade's deletion sync was switched on meanwhile), it is not run: it turns into a deletion that waits
for you. Newer restore snapshots and new personal corrections invalidate a preview. Retrying an
applied action returns its existing state instead of duplicating work. Interrupted work after a
server restart is marked as failed, not silently retried: inspect the collection before preparing
another action.

Existing synchronization safeguards, deletion limits, account checks, shared-fade permissions and
worker serialization remain in effect. A live sync re-reads the services, so its changes may differ
from an earlier dry run; an ordinary sync does exactly what a scheduled one would and never lifts
held-deletion limits.

## Available tools

| Area | Tools |
|---|---|
| Overview | `status` (incl. deletions waiting for approval), `activity`, `list_collections` |
| Library | `search_library`, `get_song`, `prepare_add_songs`, `prepare_song_removal`, `refresh_library`, `library_progress`, `stop_library_job` |
| Labels/playlists | `list_labels`, `annotate_songs`, `clear_annotations`, `prepare_playlist` |
| Fades | `list_fades`, `get_fade`, `fade_songs`, `fade_stats`, `fade_changes`, `prepare_fade`, `prepare_fade_update`, `prepare_fade_pause`, `prepare_collection_role`, `prepare_fade_deletion` |
| Sync | `preview_sync`, `get_sync_plan`, `sync_progress`, `stop_sync`, `prepare_sync` |
| Matching | `list_issues`, `issue_candidates`, `prepare_issue_resolution` (accept, ignore or unignore), `version_audit`, `version_candidates`, `prepare_version_replacement` |
| Cleanup | `cleanup_suggestions`, `prepare_dedupe`, `prepare_playlist_cleanup` |
| Time travel | `history_collections`, `history_timeline`, `history_snapshot`, `prepare_restore` |
| Proposals/results | `list_actions`, `get_action`, `apply_action`, `cancel_action` |

`prepare_fade_update` takes only the settings that change; everything left out keeps its value.
`prepare_add_songs` adds library songs to any editable collection (liked songs, library, playlist)
or to a new playlist; songs already there are not added twice.

There is also a `mood_playlist` prompt guiding the assistant through the workflow. Tool schemas
describe their parameters and limits. Library results are paginated (maximum 250 per page); follow
`next_offset`. `missing_on` compares services, `absent_from` compares a playlist/collection and
`unassigned` finds songs outside your editable playlists. These filters can be combined. Annotation
evidence and playlist selection reasons both support up to 1,500 characters.

`list_actions` and the account overview return compact action summaries, including whether a result
is available. Use `get_action` for a full preview/result. The browser loads those details when a
preview is opened, and open Crossfade tabs update live when a proposal changes.

The **Activity** tab keeps the latest 500 MCP tool outcomes per user, with connection name, tool,
status and time. It does not store access keys or tool arguments. Saved action previews contain
the music involved so you can review them later. Applying anything but a deletion follows the
client's own user-authorization flow; deletion approval in the browser is enforced independently of
the assistant.

Saved rules and automatically maintained mood playlists, automatic external enrichment, measured BPM
or valence, and reordering an existing remote playlist are not part of this version.

## Development and protocol check

Install the updated `requirements-dev.txt`. Tests use isolated SQLite databases and fake providers:

```sh
.venv/bin/python -m pytest tests/test_mcp.py -q
.venv/bin/ruff check .
cd web && npm run check && npm run build
```

For a manual protocol check, set `CROSSFADE_MCP_TOKEN` to a personal test key in your shell:

```sh
curl https://music.example.com/mcp \
  -H "Authorization: Bearer $CROSSFADE_MCP_TOKEN" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual-test","version":"1"}}}'
```

The transport is stateless and returns structured JSON tool results. The host application runs the
SDK session manager in its lifespan. See the official SDK documentation for
[ASGI integration](https://py.sdk.modelcontextprotocol.io/run/asgi/).
