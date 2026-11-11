"""Immutable previews, approval for deletions only, and idempotent MCP execution.

The HTTP handlers are also the shared application entry points: they receive explicit user/database
arguments here, so all existing ownership checks, guards and worker serialization remain in force.

Only proposals that delete something wait for the person in Crossfade: removing songs, duplicates
or playlists, deleting a fade, resetting a collection exactly, carrying out held deletions, and
switching deletion sync on (or letting it remove more at once). Everything else the assistant may
apply itself once the person asked for it.
"""
from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import music, sorting
from . import notify as alert
from .api import catalog, history, lines
from .config import config
from .db import Database
from .mcp_access import require_scope
from .models import SERVICE_NAMES
from .security import new_token
from .worker import action_context, get_worker

# operations that hand their work to the background worker; their final state arrives later
BACKGROUND = {"playlist", "add_songs", "remove_songs", "dedupe", "playlists", "restore", "sync"}
PENDING_LIMIT = 100
LIFETIME = 86400


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


class AddSongs(BaseModel):
    """Songs from the person's library into one of their collections, or into a new playlist."""
    model_config = ConfigDict(extra="forbid")
    song_ids: list[int] = Field(min_length=1, max_length=catalog.MAX_SONGS)
    source_id: int | None = Field(None, gt=0, description="An editable collection from list_collections "
                                  "(liked songs, library or playlist).")
    account_id: int | None = Field(None, gt=0, description="With new_playlist: the service account to create it on.")
    new_playlist: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def target(self):
        if bool(self.source_id) == bool(self.account_id and (self.new_playlist or "").strip()):
            raise ValueError("Choose either an existing collection (source_id) or account_id with new_playlist.")
        return self


class FadeIn(lines.LineIn):
    """A new fade. Unlike the editor, deletion sync starts off: switching it on needs the person."""
    sync_deletions: bool = Field(False, description="Remove a song everywhere once it is removed in one collection. "
                                 "Turning this on waits for the person's approval in Crossfade.")


class FadeChange(BaseModel):
    """Only the settings to change; everything left out keeps its current value."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=60)
    color: str | None = None
    icon: str | None = Field(None, pattern=r"^[a-z]{2,20}$")
    sync_deletions: bool | None = None
    interval_minutes: int | None = Field(None, ge=0, le=10080)
    delete_guard_min: int | None = Field(None, ge=0, le=100000)
    delete_guard_ratio: float | None = Field(None, ge=0, le=1)
    paused: bool | None = None
    version_policy: Literal["closest", "exact"] | None = None
    members: list[lines.MemberIn] | None = Field(
        None, min_length=1, max_length=12,
        description="Your own collections in their new order. Leave out to keep them; friends' collections stay.")


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


def _add_target(db: Database, uid: int, body: catalog.AddBody, playlists_only: bool) -> str:
    """Name of where songs go; refuses collections that can't be edited."""
    if body.source_id:
        source = next((s for s in catalog._sources(db, uid) if s["id"] == body.source_id), None)
        if not source or not source["editable"] or (playlists_only and source["kind"] != "playlist"):
            raise HTTPException(404, "Editable playlist not found." if playlists_only else "Editable collection not found.")
        return f"{SERVICE_NAMES[source['service']]}: {source['name']}"
    acc = db.one("SELECT service FROM accounts WHERE id=? AND user_id=? AND credentials IS NOT NULL",
                 (body.account_id, uid))
    if not acc:
        raise HTTPException(404, "Service is not connected.")
    return f"{SERVICE_NAMES[acc['service']]}: {body.new_playlist}"


def _receives_deletions(role: str, follow: bool) -> bool:
    return role in ("both", "receive") or (role == "send" and follow)


def _merged_fade(db: Database, uid: int, fade_id: int, changes: dict) -> tuple[dict, lines.LineIn]:
    """The fade as it would be after the change: current settings plus what the assistant set."""
    line = lines._line_or_404(db, fade_id, uid)
    current = {k: line[k] for k in ("name", "color", "icon", "interval_minutes", "delete_guard_min",
                                    "delete_guard_ratio", "version_policy")}
    current |= {"sync_deletions": bool(line["sync_deletions"]), "paused": bool(line["paused"])}
    current["members"] = [{"service": m["service"], "kind": m["kind"], "collection_id": m["collection_id"],
                           "collection_name": m["collection_name"], "role": m["role"],
                           "follow_deletions": bool(m["follow_deletions"]), "rule": m["rule"],
                           "move_sorted": m["move_sorted"]}
                          for m in lines._members(db, fade_id, uid) if m["mine"]]
    wanted = {k: v for k, v in changes.items() if v is not None}
    try:
        return line, lines.LineIn.model_validate(current | wanted)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        raise HTTPException(400, f"Invalid fade setting {field}: {first['msg']}") from exc


def _widens_deletions(db: Database, uid: int, line: dict | None, body: lines.LineIn) -> bool:
    """Deletions switched on, allowed to remove more at once, or reaching one of your collections
    that so far never lost songs through this fade – including a collection that starts letting go
    of sorted songs (removing them from liked songs is a deletion there)."""
    moving_before = {(m["service"], m["kind"], m["collection_id"]) for m in lines._members(db, line["id"], uid)
                     if m["mine"] and m["move_sorted"] and m["role"] != "receive"} if line else set()
    if any(m.move_sorted and m.role != "receive" and (m.service, m.kind, m.collection_id) not in moving_before
           for m in body.members):
        return True
    if not body.sync_deletions:
        return False
    if line is None or not line["sync_deletions"]:
        return True
    if body.delete_guard_min > line["delete_guard_min"] or body.delete_guard_ratio > line["delete_guard_ratio"]:
        return True
    before = {(m["service"], m["kind"], m["collection_id"]): _receives_deletions(m["role"], m["follow_deletions"])
              for m in lines._members(db, line["id"], uid) if m["mine"]}
    return any(before.get((m.service, m.kind, m.collection_id)) is False
               and _receives_deletions(m.role, m.follow_deletions) for m in body.members)


def validate(db: Database, user: dict, operation: str, params: dict) -> tuple[str, bool, dict, dict]:
    """Validate without remote writes. Repeated immediately before executing an action.
    Returns (scope, deletes, params, preview)."""
    uid = user["id"]
    if operation in ("playlist", "add_songs"):
        body = catalog.AddBody.model_validate(params)
        target = _add_target(db, uid, body, playlists_only=operation == "playlist")
        return "manage", False, body.model_dump(), {"songs": _selected_songs(db, uid, body.song_ids),
                                                     "count": len(set(body.song_ids)), "target": target}
    if operation == "fade_create":
        body = lines.LineIn.model_validate(params["fade"])
        lines._resolve_members(db, uid, body.members, create=False)
        return "manage", _widens_deletions(db, uid, None, body), {"fade": body.model_dump()}, body.model_dump()
    if operation == "fade_update":
        line_id = params["fade_id"]
        if "fade" in params:      # a full definition (proposals made before partial changes existed)
            line = lines._line_or_404(db, line_id, uid)
            body = lines.LineIn.model_validate(params["fade"])
            changes = params["fade"]
        else:
            changes = FadeChange.model_validate(params["changes"]).model_dump(exclude_none=True)
            line, body = _merged_fade(db, uid, line_id, changes)
        lines._resolve_members(db, uid, body.members, create=False)
        preview = {"fade": line["name"], "changes": {k: v for k, v in changes.items() if k != "members"}}
        if "members" in changes:
            preview["members"] = [{**m.model_dump(include={"service", "kind", "collection_name", "role",
                                                           "move_sorted"}),
                                   "takes": sorting.describe(m.rule)} for m in body.members]
        return "manage", _widens_deletions(db, uid, line, body), {
            "fade_id": line_id, **({"fade": params["fade"]} if "fade" in params else {"changes": changes})}, preview
    if operation in ("fade_delete", "fade_pause", "sync"):
        line_id = params["fade_id"]
        line = (lines._visible_or_404 if operation == "sync" else lines._line_or_404)(db, line_id, uid)
        if operation == "fade_pause":
            body = lines.PausedBody.model_validate({"paused": params["paused"]})
            return "manage", False, {"fade_id": line_id, **body.model_dump()}, {"fade": line["name"], **body.model_dump()}
        if operation == "sync":
            allow = bool(params.get("allow_deletions", False))
            if allow and line["user_id"] != uid:
                raise HTTPException(403, "Only the owner can confirm held deletions.")
            return "sync", allow, {"fade_id": line_id, "allow_deletions": allow}, {
                "fade": line["name"], "sync_deletions": bool(line["sync_deletions"]),
                "allow_deletions": allow, "last_plan": lines.last_plan(line_id, user, db),
                "note": "This sync reads the collections again. The fade's deletion safety limit still applies."}
        return "manage", True, {"fade_id": line_id}, {"fade": line["name"], "members": lines._members(db, line_id, uid)}
    if operation in ("resolve_issue", "ignore_issue", "restore_issue"):
        row = lines._issue(db, params["fade_id"], params["issue_id"], uid)
        if operation == "resolve_issue":
            body = lines.ResolveBody.model_validate({"target": params["target"]})
            params = {"fade_id": params["fade_id"], "issue_id": params["issue_id"], **body.model_dump()}
        else:
            if operation == "restore_issue" and row["kind"] != "ignored":
                raise HTTPException(400, "Only ignored songs can be brought back.")
            params = {"fade_id": params["fade_id"], "issue_id": params["issue_id"]}
        return "manage", False, params, {"issue": row["id"], "song_id": row["song_id"], **params}
    if operation == "member_role":
        line = lines._visible_or_404(db, params["fade_id"], uid)
        body = lines.MembershipBody.model_validate(params)
        if line["user_id"] == uid:
            member = db.one("SELECT * FROM line_members WHERE id=? AND line_id=?", (params["member_id"], line["id"]))
            if not member:
                raise HTTPException(404, "Collection not found.")
        else:
            member = lines._own_member(db, line["id"], params["member_id"], uid)
        return "manage", False, {"fade_id": line["id"], "member_id": member["id"], **body.model_dump()}, {
            "fade": line["name"], "collection": member["collection_name"], **body.model_dump()}
    if operation == "replace_version":
        lines._visible_or_404(db, params["fade_id"], uid)
        lines._song_in_fade(db, params["fade_id"], params["song_id"])
        member = lines._own_member(db, params["fade_id"], params["member_id"], uid)
        body = lines.ReplaceBody.model_validate(params)
        return "manage", False, {"fade_id": params["fade_id"], "song_id": params["song_id"], **body.model_dump()}, {
            "song_id": params["song_id"], "collection": member["collection_name"], "target": body.target,
            "note": "The chosen version is added first; the old one is removed only afterwards."}
    if operation == "remove_songs":
        body = catalog.RemoveBody.model_validate(params)
        songs = _selected_songs(db, uid, body.song_ids)
        sources = catalog._own_sources(db, uid, set(body.source_ids))
        if any(not s["editable"] for s in sources.values()):
            raise HTTPException(400, "A selected collection is not editable.")
        return "manage", True, body.model_dump(), {"songs": songs, "count": len(songs),
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
        # "exact" removes whatever came in since (read live, so possibly more than the preview shows)
        return "manage", body.mode == "exact", {"snapshot_id": params["snapshot_id"], **body.model_dump()}, {
            "snapshot": snap, "mode": body.mode, "only": body.only}
    raise HTTPException(400, "Unknown operation.")


def approve_url(action_id: str) -> str:
    return f"{config.base_url}/assistant?action={action_id}"


def changed(user_id: int) -> None:
    """Open Crossfade tabs reload their proposals (and the approval card on the home page)."""
    get_worker().bus.publish(user_id, {"type": "mcp"})


def prepare(db: Database, principal: dict, operation: str, params: dict, preview: dict | None = None) -> dict:
    scope, destructive, params, checked = validate(db, principal, operation, params)
    require_scope(principal, scope)
    if db.val("SELECT COUNT(*) FROM mcp_actions WHERE user_id=? AND state='pending' AND expires_at>?",
              (principal["id"], time.time())) >= PENDING_LIMIT:
        raise HTTPException(409, "Review or cancel pending actions before adding more.")
    aid, now = new_token(), time.time()
    db.run("INSERT INTO mcp_actions(id,user_id,token_id,operation,scope,params,preview,destructive,created_at,expires_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?)", (aid, principal["id"], principal["token_id"], operation, scope,
                                          json.dumps(params), json.dumps(preview or checked), int(destructive),
                                          now, now + LIFETIME))
    if destructive:
        alert.safely(alert.assistant_waiting, db, principal["id"], aid, operation, principal["connection"])
    changed(principal["id"])
    return get_action(db, principal["id"], aid)


def _shape(row: dict, now: float) -> dict:
    if row["state"] == "pending" and row["expires_at"] <= now:
        row["state"] = "expired"
    row["requires_browser_confirmation"] = bool(row["destructive"])
    if row["destructive"] and row["state"] == "pending":
        row["approve_url"] = approve_url(row["id"])
    return row


def action_summaries(db: Database, uid: int, limit: int = 50, offset: int = 0,
                     pending_first: bool = False) -> list[dict]:
    """List metadata without loading large proposal/result JSON; get_action serves one full detail."""
    now = time.time()
    order = "(a.state='pending' AND a.expires_at>?) DESC," if pending_first else ""
    rows = db.all(
        "SELECT a.id,a.operation,a.scope,a.token_id,a.destructive,a.state,a.created_at,a.expires_at,a.error,"
        "a.result IS NOT NULL AS has_result,t.name AS connection,t.expires_at AS token_expires FROM mcp_actions a "
        "LEFT JOIN mcp_tokens t ON t.id=a.token_id WHERE a.user_id=? ORDER BY "
        + order + "a.created_at DESC,a.id DESC LIMIT ? OFFSET ?",
        [uid, *([now] if pending_first else []), max(1, min(limit, 250)), max(0, offset)])
    for row in rows:
        row["has_result"] = bool(row["has_result"])
        # a proposal of a connection that ended can never run any more
        if row["state"] == "pending" and (row.pop("token_expires") or 0) <= now:
            row["state"] = "expired"
        row.pop("token_expires", None)
        _shape(row, now)
    return rows


def brief(operation: str, preview: dict | None) -> dict:
    """The gist of a proposal in a few numbers and names, for a card that is read at a glance."""
    p = preview or {}
    out: dict = {}
    if isinstance(p.get("fade"), str):
        out["fade"] = p["fade"]
    elif isinstance(p.get("name"), str) and operation == "fade_create":
        out["fade"] = p["name"]
    if operation == "remove_songs":
        out["songs"] = p.get("count") or len(p.get("songs") or [])
        out["collections"] = [s["name"] for s in p.get("sources") or []]
    elif operation == "dedupe":
        out["songs"] = len(p.get("groups") or []) + len(p.get("copies") or [])
    elif operation == "playlists":
        selection = p.get("selection") or {}
        out["playlists"] = len(selection.get("delete") or []) + len(selection.get("merge") or [])
        out["collections"] = [s["name"] for s in p.get("sources") or []
                              if s["id"] in set(selection.get("delete") or []) | {m[0] for m in selection.get("merge") or []}]
    elif operation == "restore":
        snap = p.get("snapshot") or {}
        out["songs"] = len(snap.get("extra") or [])
        out["taken_at"] = snap.get("taken_at")
    elif operation == "sync":
        held = ((p.get("last_plan") or {}).get("plan") or {}).get("held") or []
        out["songs"] = len(held)
    elif operation == "fade_delete":
        out["collections"] = [m["collection_name"] for m in p.get("members") or []]
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def awaiting(db: Database, uid: int) -> list[dict]:
    """Deletions an active connection proposed and only the person can let happen."""
    rows = db.all("""SELECT a.id, a.operation, a.created_at, a.preview, t.name AS connection FROM mcp_actions a
                     JOIN mcp_tokens t ON t.id=a.token_id
                     WHERE a.user_id=? AND a.state='pending' AND a.destructive=1 AND a.expires_at>?
                       AND t.expires_at>? ORDER BY a.created_at DESC LIMIT 50""",
                  (uid, time.time(), time.time()))
    for row in rows:
        row["brief"] = brief(row["operation"], json.loads(row.pop("preview") or "null"))
    return rows


def get_action(db: Database, uid: int, action_id: str) -> dict:
    row = db.one("SELECT * FROM mcp_actions WHERE id=? AND user_id=?", (action_id, uid))
    if not row:
        raise HTTPException(404, "Action not found.")
    row.pop("params")
    for key in ("preview", "result"):
        row[key] = json.loads(row[key]) if row[key] else None
    _shape(row, time.time())
    if row.get("approve_url"):
        row["review_in"] = "Crossfade → Account → AI assistants → Approvals, or open approve_url"
    return row


def apply_action(db: Database, user: dict, action_id: str, browser: bool = False) -> dict:
    row = db.one("SELECT * FROM mcp_actions WHERE id=? AND user_id=?", (action_id, user["id"]))
    if not row:
        raise HTTPException(404, "Action not found.")
    if not browser:
        require_scope(user, row["scope"])
        if row["token_id"] != user["token_id"]:
            raise HTTPException(403, "This action belongs to a different connection.")
    if row["state"] != "pending":
        return get_action(db, user["id"], action_id)
    if row["expires_at"] <= time.time():
        raise HTTPException(409, "This preview expired. Prepare a new action.")
    # A revoked/expired connection must not leave executable proposals behind.
    if not db.val("SELECT 1 FROM mcp_tokens WHERE id=? AND user_id=? AND expires_at>?",
                  (row["token_id"], user["id"], time.time())):
        raise HTTPException(403, "The connection that prepared this action is no longer active.")
    params = json.loads(row["params"])
    _, deletes, current_params, checked = validate(db, user, row["operation"], params)
    if deletes and not row["destructive"]:
        # Something changed since the preview (e.g. the fade now syncs deletions): from here on
        # only the person can let it happen, after seeing that it deletes.
        db.run("UPDATE mcp_actions SET destructive=1 WHERE id=?", (action_id,))
        alert.safely(alert.assistant_waiting, db, user["id"], action_id, row["operation"],
                     db.val("SELECT name FROM mcp_tokens WHERE id=?", (row["token_id"],)) or "Assistant")
        changed(user["id"])
        if browser:
            raise HTTPException(409, "This would now delete something. Look at the preview again and allow it.")
        raise HTTPException(409, "This now deletes something, so the person has to confirm it in Crossfade: "
                            + approve_url(action_id))
    if deletes and not browser:
        raise HTTPException(403, "Deleting needs the person's confirmation in Crossfade: " + approve_url(action_id))
    preview = json.loads(row["preview"])
    if row["operation"] == "restore" and checked["snapshot"]["latest"] != preview["snapshot"]["latest"]:
        raise HTTPException(409, "The collection changed since this preview. Prepare a new restore preview.")
    if row["operation"] == "playlist" and (preview.get("mood") or preview.get("genre")):
        ids = current_params["song_ids"]
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
               ("queued" if row["operation"] in BACKGROUND else "ok", json.dumps(result), action_id))
    except Exception as exc:
        db.run("UPDATE mcp_actions SET state='error',error=? WHERE id=? AND state='executing'",
               (str(exc.detail) if isinstance(exc, HTTPException) else "The action failed; inspect Crossfade's logs.",
                action_id))
        raise
    finally:
        action_context.reset(context)
        changed(user["id"])
    return get_action(db, user["id"], action_id)


def cancel(db: Database, uid: int, action_id: str) -> dict:
    db.run("UPDATE mcp_actions SET state='cancelled' WHERE id=? AND user_id=? AND state='pending'", (action_id, uid))
    changed(uid)
    return get_action(db, uid, action_id)


def _execute(db: Database, user: dict, operation: str, p: dict) -> dict:
    if operation in ("playlist", "add_songs"):
        return catalog.add(catalog.AddBody.model_validate(p), user, db)
    if operation == "remove_songs":
        return catalog.remove(catalog.RemoveBody.model_validate(p), user, db)
    if operation == "fade_create":
        return lines.create_line(lines.LineIn.model_validate(p["fade"]), user, db)
    if operation == "fade_update":
        body = (lines.LineIn.model_validate(p["fade"]) if "fade" in p
                else _merged_fade(db, user["id"], p["fade_id"], p["changes"])[1])
        return lines.update_line(p["fade_id"], body, user, db)
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
    if operation == "restore_issue":
        return lines.restore_issue(p["fade_id"], p["issue_id"], user, db)
    if operation == "member_role":
        return lines.set_member_role(p["fade_id"], p["member_id"], lines.MembershipBody.model_validate(p), user, db)
    if operation == "replace_version":
        return lines.replace_version(p["fade_id"], p["song_id"], lines.ReplaceBody.model_validate(p), user, db)
    if operation == "dedupe":
        return catalog.dedupe(catalog.DedupeBody.model_validate(p), user, db)
    if operation == "playlists":
        return catalog.playlists(catalog.PlaylistsBody.model_validate(p), user, db)
    if operation == "restore":
        return history.restore(p["snapshot_id"], history.RestoreBody.model_validate(p), user, db)
    raise HTTPException(400, "Unknown operation.")
