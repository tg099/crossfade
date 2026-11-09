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
3. Give the connection a name and an expiry (7, 30, 90 or 365 days). Reading is always allowed.
   Enable **Manage music and playlists** for mood playlists and **Synchronize** for sync operations.
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

A song can have several moods, genres and personal tags. The assistant can either select songs
explicitly for a free-form description or reuse an exact saved mood/genre label. Different languages
and synonyms are interpreted by the assistant; stored labels use exact, case-insensitive matching.

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

Open **Account → AI assistants → Tags**, search for a song and edit its moods, genres or tags.
Separate several labels with commas. These browser corrections are authoritative: assistants cannot
overwrite them. For a free-form mood, the assistant must name the fitting saved personal labels in
each corrected song's `matched_moods`; a contradictory selection is skipped. A personal empty mood
list excludes the song from mood playlists. A correction made after a proposal requires a fresh
playlist preview. Use **Forget classification** to allow assistant classification again.

Annotations are per person, even for a song whose identity is shared with another user. They survive
song identity merges, with personal corrections taking precedence over assistant estimates.

## Previews, approval and progress

All music-editing tools prepare immutable proposals. A proposal is not an executed change.

1. The assistant prepares an action and shows the preview.
2. For non-destructive actions, it calls `apply_action` on your instruction. You can also apply them
   from **Account → AI assistants → Actions**.
3. Destructive operations require **Apply** in the Crossfade browser UI. MCP cannot approve its own
   deletions, replacements, playlist merges/deletions, fade configuration changes or restores.
   A sync on a deletion-enabled fade, including approval of held deletions, also requires this step.
   Resuming a deletion-enabled fade is treated the same way.
4. The assistant polls `get_action` until the worker reports a final state and explains any failures.

Each proposal expires after 24 hours and can be cancelled. A token may execute only proposals made
by that connection and must have the required permission. Crossfade checks ownership and access again
at execution. Changed deletion settings, newer restore snapshots and new personal corrections can
invalidate a preview. Retrying an applied action returns its existing state instead of duplicating work.
Interrupted work after a server restart is marked as failed, not silently retried: inspect the
collection before preparing another action.

Existing synchronization safeguards, deletion limits, account checks, shared-fade permissions and
worker serialization remain in effect. A live sync re-reads the services, so its changes may differ
from an earlier dry run; approving ordinary syncs does not automatically lift held-deletion limits.

## Available tools

| Area | Tools |
|---|---|
| Overview | `status`, `list_collections` |
| Library | `search_library`, `get_song`, `refresh_library`, `library_progress`, `stop_library_job` |
| Classification/playlists | `annotate_songs`, `prepare_playlist` |
| Fades | `list_fades`, `get_fade`, `fade_changes`, `prepare_fade`, `prepare_fade_pause`, `prepare_fade_deletion` |
| Sync | `preview_sync`, `get_sync_plan`, `sync_progress`, `stop_sync`, `prepare_sync` |
| Matching | `list_issues`, `issue_candidates`, `prepare_issue_resolution`, `version_audit`, `version_candidates`, `prepare_version_replacement` |
| Cleanup | `cleanup_suggestions`, `prepare_dedupe`, `prepare_playlist_cleanup`, `prepare_song_removal` |
| Time travel | `history_collections`, `history_timeline`, `history_snapshot`, `prepare_restore` |
| Proposals/results | `list_actions`, `get_action`, `apply_action`, `cancel_action` |

There is also a `mood_playlist` prompt guiding the assistant through the workflow. Tool schemas
describe their parameters and limits. Library results are paginated (maximum 250 per page); follow
`next_offset`. `missing_on` compares services, `absent_from` compares a playlist/collection and
`unassigned` finds songs outside your editable playlists. These filters can be combined. Annotation
evidence and playlist selection reasons both support up to 1,500 characters.

`list_actions` and the account overview return compact action summaries, including whether a result
is available. Use `get_action` for a full preview/result. The browser loads those details when their
disclosure is opened and refreshes an open detail when the action's state changes.

The **Activity** tab keeps the latest 500 MCP tool outcomes per user, with connection name, tool,
status and time. It does not store access keys or tool arguments. Saved action previews contain
the music involved so you can review them later. Non-destructive application follows the client's
user-authorization flow; only destructive browser approval is enforced independently of the assistant.

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
