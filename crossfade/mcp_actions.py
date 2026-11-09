"""Immutable previews, explicit destructive approval and idempotent MCP execution.

The HTTP handlers are also the shared application entry points: they receive explicit user/database
arguments here, so all existing ownership checks, guards and worker serialization remain in force.
"""
from __future__ import annotations

import json
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import music
from .api import catalog, history, lines
from .db import Database
from .mcp_access import require_scope
from .models import SERVICE_NAMES
from .security import new_token
from .worker import action_context, get_worker


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=music.MAX_REASON_LENGTH)
    confidence: float = Field(ge=0, le=1)
    matched_moods: list[str] = Field(default_factory=list, max_length=20,
                                    description="For a free-form mood, list the saved personal mood labels that fit.")


class PlaylistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    account_id: int | None = Field(None, gt=0)
    source_id: int | None = Field(None, gt=0)
    mood: str = Field("", max_length=300)
    genre: str = Field("", max_length=80)
    selections: list[Selection] = Field(default_factory=list, max_length=2000)
    exclude_song_ids: list[int] = Field(default_factory=list, max_length=2000)
    max_tracks: int = Field(50, ge=1, le=2000)
    duration_minutes: int | None = Field(None, ge=1, le=1440)
    min_confidence: float = Field(0.7, ge=0, le=1)

    @model_validator(mode="after")
    def target(self):
        if not self.name.strip() or bool(self.account_id) == bool(self.source_id):
            raise ValueError("Choose either an account for a new playlist or an existing playlist source.")
        if not self.selections and not (self.mood.strip() or self.genre.strip()):
            raise ValueError("Choose songs or a saved mood/genre.")
        return self


def playlist_preview(db: Database, user: dict, request: PlaylistRequest) -> tuple[dict, dict]:
    if request.source_id:
        target = next((s for s in catalog._sources(db, user["id"]) if s["id"] == request.source_id), None)
        if not target or target["kind"] != "playlist" or not target["editable"]:
            raise HTTPException(404, "Choose one of your editable playlists.")
        target_name = target["name"]
    else:
        acc = db.one("SELECT service FROM accounts WHERE id=? AND user_id=? AND credentials IS NOT NULL",
                     (request.account_id, user["id"]))
        if not acc:
            raise HTTPException(404, "Service is not connected.")
        target_name = f"{SERVICE_NAMES[acc['service']]}: {request.name.strip()}"
    if request.selections:
        selected = list({s.song_id: s for s in request.selections}.values())
    else:
        selected, offset = [], 0
        # Exact saved labels are reproducible. Free-form interpretation belongs to the assistant,
        # which must supply explicit song ids, reasons and confidence rather than title keywords.
        while len(selected) < 2000:
            page = music.library(db, user["id"], mood=request.mood, genre=request.genre, offset=offset, limit=250)
            for song in page["songs"]:
                annotation = song["annotation"]
                selected.append(Selection(song_id=song["id"], reason=annotation["evidence"],
                                          matched_moods=annotation["moods"],
                                          confidence=1 if song["origin"] == "user" else annotation["confidence"]))
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        selected = selected[:2000]
    songs = music.selected_songs(db, user["id"], [s.song_id for s in selected])
    exclusions = set(request.exclude_song_ids)
    chosen, skipped, seconds, unknown = [], [], 0, 0
    budget = request.duration_minutes * 60 if request.duration_minutes else None
    for selection in selected:
        details = songs[selection.song_id]
        song = {key: details[key] for key in ("id", "title", "artist", "album", "duration")}
        annotation = details["annotation"]
        confidence = selection.confidence
        # A correction must also constrain explicit assistant selections, not only auto selection.
        if annotation and details["origin"] == "user":
            data = annotation
            mood_labels = {s.strip().casefold() for s in selection.matched_moods} | {request.mood.casefold().strip()}
            if ((request.mood and not mood_labels.intersection(data["moods"]))
                    or (request.genre and request.genre.casefold().strip() not in data["genres"])):
                skipped.append({**song, "reason": "personal_correction"})
                continue
            confidence = 1
        why = None
        if song["id"] in exclusions:
            why = "excluded"
        elif confidence < request.min_confidence:
            why = "uncertain"
        elif len(chosen) >= request.max_tracks:
            why = "track_limit"
        elif budget is not None and (not song["duration"] or seconds + song["duration"] > budget):
            why = "unknown_duration" if not song["duration"] else "duration_limit"
        if why:
            skipped.append({**song, "reason": why})
            continue
        chosen.append({**song, "reason": selection.reason, "confidence": confidence})
        seconds += song["duration"] or 0
        unknown += int(not song["duration"])
    if not chosen:
        raise HTTPException(400, "No matching songs. Read the library, classify known songs with evidence, "
                            "or supply an explicit selection. Do not guess moods from titles.")
    body = {"song_ids": [s["id"] for s in chosen], "source_id": request.source_id,
            "account_id": request.account_id, "new_playlist": request.name.strip() if request.account_id else None}
    preview = {"name": request.name.strip(), "target": target_name, "mood": request.mood, "genre": request.genre,
               "songs": chosen, "count": len(chosen), "known_duration_seconds": seconds,
               "unknown_duration_count": unknown, "skipped": skipped,
               "note": "Mood/genre are estimates, not audio analysis. Cross-service matches can be unavailable. "
                       "The worker reports missing tracks and failures; no existing songs will be removed."}
    return body, preview


def _selected_songs(db: Database, uid: int, ids: list[int]) -> list[dict]:
    songs = music.selected_songs(db, uid, ids)
    return [{key: songs[sid][key] for key in ("id", "title", "artist")} for sid in dict.fromkeys(ids)]


def validate(db: Database, user: dict, operation: str, params: dict) -> tuple[str, bool, dict, dict]:
    """Validate without remote writes. Repeated immediately before executing an approved action."""
    uid = user["id"]
    if operation == "playlist":
        body = catalog.AddBody.model_validate(params)
        songs = _selected_songs(db, uid, body.song_ids)
        if body.source_id:
            source = next((s for s in catalog._sources(db, uid) if s["id"] == body.source_id), None)
            if not source or not source["editable"] or source["kind"] != "playlist":
                raise HTTPException(404, "Editable playlist not found.")
            target = source["name"]
        else:
            acc = db.one("SELECT service FROM accounts WHERE id=? AND user_id=? AND credentials IS NOT NULL",
                         (body.account_id, uid))
            if not acc:
                raise HTTPException(404, "Service is not connected.")
            target = f"{SERVICE_NAMES[acc['service']]}: {body.new_playlist}"
        return "manage", False, body.model_dump(), {"songs": songs, "target": target}
    if operation in ("fade_create", "fade_update"):
        line_id = params.get("fade_id")
        body = lines.LineIn.model_validate(params["fade"])
        if operation == "fade_update":
            lines._line_or_404(db, line_id, uid)
        lines._resolve_members(db, uid, body.members, create=False)
        return "manage", True, {"fade_id": line_id, "fade": body.model_dump()}, body.model_dump()
    if operation in ("fade_delete", "fade_pause", "sync"):
        line_id = params["fade_id"]
        line = (lines._visible_or_404 if operation == "sync" else lines._line_or_404)(db, line_id, uid)
        if operation == "fade_pause":
            body = lines.PausedBody.model_validate({"paused": params["paused"]})
            return "manage", bool(not body.paused and line["sync_deletions"]), {
                "fade_id": line_id, **body.model_dump()}, {"fade": line["name"], **body.model_dump()}
        if operation == "sync":
            allow = bool(params.get("allow_deletions", False))
            if allow and line["user_id"] != uid:
                raise HTTPException(403, "Only the owner can confirm held deletions.")
            return "sync", bool(line["sync_deletions"] or allow), {"fade_id": line_id, "allow_deletions": allow}, {
                "fade": line["name"], "sync_deletions": bool(line["sync_deletions"]),
                "allow_deletions": allow, "last_plan": lines.last_plan(line_id, user, db),
                "note": "This run reads the collections again. The existing deletion safety limits still apply."}
        return "manage", True, {"fade_id": line_id}, {"fade": line["name"], "members": lines._members(db, line_id, uid)}
    if operation in ("resolve_issue", "ignore_issue"):
        row = lines._issue(db, params["fade_id"], params["issue_id"], uid)
        if operation == "resolve_issue":
            body = lines.ResolveBody.model_validate({"target": params["target"]})
            params = {"fade_id": params["fade_id"], "issue_id": params["issue_id"], **body.model_dump()}
        else:
            params = {"fade_id": params["fade_id"], "issue_id": params["issue_id"]}
        return "manage", False, params, {"issue": row["id"], "song_id": row["song_id"], **params}
    if operation == "replace_version":
        lines._visible_or_404(db, params["fade_id"], uid)
        lines._song_in_fade(db, params["fade_id"], params["song_id"])
        member = lines._own_member(db, params["fade_id"], params["member_id"], uid)
        body = lines.ReplaceBody.model_validate(params)
        return "manage", True, {"fade_id": params["fade_id"], "song_id": params["song_id"], **body.model_dump()}, {
            "song_id": params["song_id"], "collection": member["collection_name"], "target": body.target}
    if operation == "remove_songs":
        body = catalog.RemoveBody.model_validate(params)
        songs = _selected_songs(db, uid, body.song_ids)
        sources = catalog._own_sources(db, uid, set(body.source_ids))
        if any(not s["editable"] for s in sources.values()):
            raise HTTPException(400, "A selected collection is not editable.")
        return "manage", True, body.model_dump(), {"songs": songs,
                                                    "sources": [{"name": s["name"], "service": s["service"]}
                                                                for s in sources.values()]}
    if operation == "dedupe":
        body = catalog.DedupeBody.model_validate(params)
        if not body.groups and not body.copies:
            raise HTTPException(400, "Nothing selected.")
        for group in body.groups:
            music.require_songs(db, uid, group.songs)
            if db.val("SELECT song_id FROM song_refs WHERE service=? AND ref_id=?", group.keep) not in group.songs:
                raise HTTPException(400, "The chosen version does not belong to these songs.")
        catalog._own_sources(db, uid, {c[0] for c in body.copies})
        return "manage", True, body.model_dump(), body.model_dump()
    if operation == "playlists":
        body = catalog.PlaylistsBody.model_validate(params)
        gone = set(body.delete) | {m[0] for m in body.merge}
        if not gone:
            raise HTTPException(400, "Nothing selected.")
        sources = catalog._own_sources(db, uid, gone | {m[1] for m in body.merge})
        details = {s["id"]: s for s in catalog._sources(db, uid)}
        for sid in gone:
            if sources[sid]["kind"] != "playlist" or not sources[sid]["editable"] or details[sid]["lines"]:
                raise HTTPException(400, "A selected playlist is not editable or is part of a fade.")
        for a, b in body.merge:
            if a == b or sources[a]["account_id"] != sources[b]["account_id"] or not sources[b]["editable"]:
                raise HTTPException(400, "Playlists must be editable and belong to the same account.")
        return "manage", True, body.model_dump(), {"selection": body.model_dump(),
                                                    "sources": [details[sid] for sid in sources]}
    if operation == "restore":
        snap = history.snapshot(params["snapshot_id"], user, db)
        body = history.RestoreBody.model_validate(params)
        if body.mode == "exact" and body.only is not None:
            raise HTTPException(400, "An exact restore applies to the complete snapshot.")
        return "manage", True, {"snapshot_id": params["snapshot_id"], **body.model_dump()}, {
            "snapshot": snap, "mode": body.mode, "only": body.only}
    raise HTTPException(400, "Unknown operation.")


def prepare(db: Database, principal: dict, operation: str, params: dict, preview: dict | None = None) -> dict:
    scope, destructive, params, checked = validate(db, principal, operation, params)
    require_scope(principal, scope)
    if db.val("SELECT COUNT(*) FROM mcp_actions WHERE user_id=? AND state='pending' AND expires_at>?",
              (principal["id"], time.time())) >= 100:
        raise HTTPException(409, "Review or cancel pending actions before adding more.")
    aid, now = new_token(), time.time()
    db.run("INSERT INTO mcp_actions(id,user_id,token_id,operation,scope,params,preview,destructive,created_at,expires_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?)", (aid, principal["id"], principal["token_id"], operation, scope,
                                          json.dumps(params), json.dumps(preview or checked), int(destructive),
                                          now, now + 86400))
    return get_action(db, principal["id"], aid)


def action_summaries(db: Database, uid: int, limit: int = 50, offset: int = 0,
                     pending_first: bool = False) -> list[dict]:
    """List metadata without loading large proposal/result JSON; get_action serves one full detail."""
    now = time.time()
    order = "(state='pending' AND expires_at>?) DESC," if pending_first else ""
    rows = db.all(
        "SELECT id,operation,scope,token_id,destructive,state,created_at,expires_at,error,"
        "result IS NOT NULL AS has_result FROM mcp_actions WHERE user_id=? ORDER BY "
        + order + "created_at DESC,id DESC LIMIT ? OFFSET ?",
        [uid, *([now] if pending_first else []), max(1, min(limit, 250)), max(0, offset)])
    for row in rows:
        if row["state"] == "pending" and row["expires_at"] <= now:
            row["state"] = "expired"
        row["has_result"] = bool(row["has_result"])
        row["requires_browser_confirmation"] = bool(row["destructive"])
    return rows


def get_action(db: Database, uid: int, action_id: str) -> dict:
    row = db.one("SELECT * FROM mcp_actions WHERE id=? AND user_id=?", (action_id, uid))
    if not row:
        raise HTTPException(404, "Action not found.")
    if row["state"] == "pending" and row["expires_at"] <= time.time():
        row["state"] = "expired"
    row.pop("params")
    for key in ("preview", "result"):
        row[key] = json.loads(row[key]) if row[key] else None
    row["requires_browser_confirmation"] = bool(row["destructive"])
    row["review_in"] = "Account → AI assistants → Pending actions"
    return row


def apply_action(db: Database, user: dict, action_id: str, browser: bool = False) -> dict:
    row = db.one("SELECT * FROM mcp_actions WHERE id=? AND user_id=?", (action_id, user["id"]))
    if not row:
        raise HTTPException(404, "Action not found.")
    if not browser:
        require_scope(user, row["scope"])
        if row["token_id"] != user["token_id"]:
            raise HTTPException(403, "This action belongs to a different connection.")
        if row["destructive"]:
            raise HTTPException(403, "Confirm this action in Crossfade under Account → AI assistants.")
    if row["state"] != "pending":
        return get_action(db, user["id"], action_id)
    if row["expires_at"] <= time.time():
        raise HTTPException(409, "This preview expired. Prepare a new action.")
    # A revoked/expired connection must not leave executable proposals behind.
    if not db.val("SELECT 1 FROM mcp_tokens WHERE id=? AND user_id=? AND expires_at>?",
                  (row["token_id"], user["id"], time.time())):
        raise HTTPException(403, "The connection that prepared this action is no longer active.")
    params = json.loads(row["params"])
    _, destructive_now, current_preview_params, checked = validate(db, user, row["operation"], params)
    if destructive_now != bool(row["destructive"]):
        raise HTTPException(409, "The fade settings changed. Prepare a new preview before applying.")
    preview = json.loads(row["preview"])
    if row["operation"] == "restore" and checked["snapshot"]["latest"] != preview["snapshot"]["latest"]:
        raise HTTPException(409, "The collection changed since this preview. Prepare a new restore preview.")
    if row["operation"] == "playlist" and (preview.get("mood") or preview.get("genre")):
        ids = current_preview_params["song_ids"]
        for start in range(0, len(ids), 400):
            part = ids[start:start + 400]
            if db.val("SELECT 1 FROM music_annotations WHERE user_id=? AND origin='user' AND updated_at>? "
                      "AND song_id IN (" + ",".join("?" * len(part)) + ") LIMIT 1",
                      [user["id"], row["created_at"], *part]):
                raise HTTPException(409, "Your song classifications changed. Prepare a new playlist preview.")
    if get_worker().restoring:
        raise HTTPException(409, "A database restore is in progress.")
    # Atomic claim prevents repeated tool calls, multiple tabs or retries creating duplicate playlists.
    if db.run("UPDATE mcp_actions SET state='executing' WHERE id=? AND state='pending'", (action_id,)) != 1:
        return get_action(db, user["id"], action_id)
    context = action_context.set(action_id)
    try:
        result = _execute(db, user, row["operation"], params)
        # Background work may already have finished. Never overwrite its final result.
        db.run("UPDATE mcp_actions SET state=?,result=? WHERE id=? AND state='executing'",
               ("queued" if row["operation"] in {"playlist", "remove_songs", "dedupe", "playlists", "restore", "sync"}
                else "ok", json.dumps(result), action_id))
    except Exception as exc:
        db.run("UPDATE mcp_actions SET state='error',error=? WHERE id=? AND state='executing'",
               (str(exc.detail) if isinstance(exc, HTTPException) else "The action failed; inspect Crossfade's logs.",
                action_id))
        raise
    finally:
        action_context.reset(context)
    return get_action(db, user["id"], action_id)


def _execute(db: Database, user: dict, operation: str, p: dict) -> dict:
    if operation == "playlist":
        return catalog.add(catalog.AddBody.model_validate(p), user, db)
    if operation == "remove_songs":
        return catalog.remove(catalog.RemoveBody.model_validate(p), user, db)
    if operation == "fade_create":
        return lines.create_line(lines.LineIn.model_validate(p["fade"]), user, db)
    if operation == "fade_update":
        return lines.update_line(p["fade_id"], lines.LineIn.model_validate(p["fade"]), user, db)
    if operation == "fade_delete":
        return lines.delete_line(p["fade_id"], user, db)
    if operation == "fade_pause":
        return lines.set_paused(p["fade_id"], lines.PausedBody(paused=p["paused"]), user, db)
    if operation == "sync":
        return lines.run_line(p["fade_id"], lines.RunBody(allow_deletions=p["allow_deletions"]), user, db)
    if operation == "resolve_issue":
        return lines.resolve_issue(p["fade_id"], p["issue_id"], lines.ResolveBody(target=p["target"]), user, db)
    if operation == "ignore_issue":
        return lines.ignore_issue(p["fade_id"], p["issue_id"], user, db)
    if operation == "replace_version":
        return lines.replace_version(p["fade_id"], p["song_id"], lines.ReplaceBody.model_validate(p), user, db)
    if operation == "dedupe":
        return catalog.dedupe(catalog.DedupeBody.model_validate(p), user, db)
    if operation == "playlists":
        return catalog.playlists(catalog.PlaylistsBody.model_validate(p), user, db)
    if operation == "restore":
        return history.restore(p["snapshot_id"], history.RestoreBody.model_validate(p), user, db)
    raise HTTPException(400, "Unknown operation.")
