"""Friends and invitations into fades. See crossfade/sharing.py for the privacy rules."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import notify as alert
from ..db import Database
from ..sharing import are_friends, end_sharing, friendship, notify, public_person, shared_lines
from ..worker import get_worker
from .deps import current_user, db_dep
from .lines import MemberIn, _resolve_members, join_request

router = APIRouter(prefix="/api/friends", tags=["friends"])


def overview(db: Database, uid: int) -> dict:
    rows = db.all("""SELECT f.*, u.id AS uid, u.username, u.display_name FROM friendships f
                     JOIN users u ON u.id = CASE WHEN f.requester_id=? THEN f.addressee_id ELSE f.requester_id END
                     WHERE f.requester_id=? OR f.addressee_id=?
                     ORDER BY u.display_name COLLATE NOCASE""", (uid, uid, uid))
    friends, incoming, outgoing = [], [], []
    for r in rows:
        person = {"id": r["uid"], "username": r["username"], "display_name": r["display_name"]}
        if r["status"] == "accepted":
            shared = db.val("""SELECT COUNT(DISTINCT l.id) FROM lines l JOIN line_members m ON m.line_id=l.id
                               JOIN accounts a ON a.id=m.account_id
                               WHERE (l.user_id=? AND a.user_id=?) OR (l.user_id=? AND a.user_id=?)""",
                            (uid, r["uid"], r["uid"], uid))
            friends.append({**person, "since": r["accepted_at"], "shared": shared})
        elif r["requester_id"] == uid:
            outgoing.append({"id": r["id"], "user": person, "created_at": r["created_at"]})
        else:
            incoming.append({"id": r["id"], "user": person, "created_at": r["created_at"]})
    invites = db.all("""SELECT i.id, i.created_at, i.request, i.requested_at, l.name, l.icon, l.color, u.id AS uid, u.username, u.display_name
                        FROM fade_invites i JOIN lines l ON l.id=i.line_id JOIN users u ON u.id=i.from_user
                        WHERE i.to_user=? ORDER BY i.created_at DESC""", (uid,))
    return {
        "friends": friends, "incoming": incoming, "outgoing": outgoing,
        # only what is needed to decide: the fade's name and look, and who asks
        "invites": [{"id": i["id"], "created_at": i["created_at"],
                     "fade": {"name": i["name"], "icon": i["icon"], "color": i["color"]},
                     "from": {"id": i["uid"], "username": i["username"], "display_name": i["display_name"]},
                     # what you asked to join with, while the owner hasn't decided yet
                     "request": join_request(i)}
                    for i in invites],
    }


BOARD_DAYS = 7


@router.get("/board")
def board(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Friends ranked by the songs they brought in over the last 7 days: songs that came from one
    of their collections and were added elsewhere by Crossfade. Only a number per person, never
    which songs or which fades. Whoever turns it off is left out and doesn't see the others either."""
    me = db.one("SELECT share_stats FROM users WHERE id=?", (user["id"],))
    if not me or not me["share_stats"]:
        return {"enabled": False, "days": BOARD_DAYS, "people": []}
    people = [r for r in db.all(
        """SELECT u.id, u.username, u.display_name FROM users u WHERE u.share_stats=1 AND (u.id=? OR u.id IN (
             SELECT CASE WHEN requester_id=? THEN addressee_id ELSE requester_id END FROM friendships
             WHERE status='accepted' AND (requester_id=? OR addressee_id=?)))""",
        (user["id"], user["id"], user["id"], user["id"]))]
    if len(people) < 2:                      # a ranking of one says nothing
        return {"enabled": True, "days": BOARD_DAYS, "people": []}
    ids = [p["id"] for p in people]
    counts = {r["user_id"]: r["n"] for r in db.all(
        f"""SELECT a.user_id, COUNT(DISTINCT c.song_id) AS n FROM changes c
             JOIN line_members m ON m.line_id=c.line_id AND m.id<>c.member_id
             JOIN accounts a ON a.id=m.account_id
             JOIN member_items mi ON mi.member_id=m.id AND mi.song_id=c.song_id AND mi.confirmed=1
             WHERE c.action='add' AND c.ok=1 AND c.ts > ? AND a.user_id IN ({','.join('?' * len(ids))})
             AND NOT EXISTS (SELECT 1 FROM changes c2 WHERE c2.member_id=m.id AND c2.song_id=c.song_id
                             AND c2.action='add' AND c2.ok=1)
             GROUP BY a.user_id""", (time.time() - BOARD_DAYS * 86400, *ids))}
    rows = sorted(({**public_person(p), "songs": counts.get(p["id"], 0), "me": p["id"] == user["id"]} for p in people),
                  key=lambda r: (-r["songs"], r["display_name"].lower()))
    return {"enabled": True, "days": BOARD_DAYS, "people": rows}


@router.get("")
def get_friends(user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    return overview(db, user["id"])


class RequestBody(BaseModel):
    username: str = Field(min_length=1, max_length=60)


@router.post("")
def request_friend(body: RequestBody, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    uid = user["id"]
    other = db.one("SELECT id, username, display_name FROM users WHERE username=? COLLATE NOCASE",
                   (body.username.strip().lstrip("@"),))
    if not other:
        raise HTTPException(404, "Nobody has this username.")
    if other["id"] == uid:
        raise HTTPException(400, "That's you.")
    f = friendship(db, uid, other["id"])
    if f and f["status"] == "accepted":
        raise HTTPException(409, "You're already friends.")
    if f and f["requester_id"] == uid:
        raise HTTPException(409, "The request has already been sent.")
    if f:   # they asked first: asking back is saying yes
        db.run("UPDATE friendships SET status='accepted', accepted_at=? WHERE id=?", (time.time(), f["id"]))
        alert.safely(alert.social, db, other["id"], "friend_accepted", name=user["display_name"])
    else:
        db.run("INSERT INTO friendships(requester_id,addressee_id,status,created_at) VALUES(?,?,'pending',?)",
               (uid, other["id"], time.time()))
        alert.safely(alert.social, db, other["id"], "friend_request", name=user["display_name"],
                     username=user["username"])
    notify([other["id"]])
    return overview(db, uid)


def _request(db: Database, request_id: int, uid: int) -> dict:
    f = db.one("SELECT * FROM friendships WHERE id=? AND status='pending' AND (requester_id=? OR addressee_id=?)",
               (request_id, uid, uid))
    if not f:
        raise HTTPException(404, "Request not found.")
    return f


@router.post("/requests/{request_id}/accept")
def accept_request(request_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    f = _request(db, request_id, user["id"])
    if f["addressee_id"] != user["id"]:
        raise HTTPException(403, "Only the person who was asked can accept.")
    db.run("UPDATE friendships SET status='accepted', accepted_at=? WHERE id=?", (time.time(), request_id))
    alert.safely(alert.social, db, f["requester_id"], "friend_accepted", name=user["display_name"])
    notify([f["requester_id"]])
    return overview(db, user["id"])


@router.delete("/requests/{request_id}")
def drop_request(request_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Decline an incoming request or withdraw your own."""
    f = _request(db, request_id, user["id"])
    db.run("DELETE FROM friendships WHERE id=?", (request_id,))
    notify([f["requester_id"], f["addressee_id"]])
    return overview(db, user["id"])


@router.delete("/{friend_id}")
def unfriend(friend_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    uid = user["id"]
    if not are_friends(db, uid, friend_id):
        raise HTTPException(404, "You're not friends.")
    affected = shared_lines(db, uid, friend_id)
    if any(get_worker().job(lid) for lid in affected):
        raise HTTPException(409, "A shared fade is syncing right now. Wait until it's done.")
    end_sharing(db, uid, friend_id)
    notify([friend_id], "fades" if affected else "friends")
    return overview(db, uid)


# ---------------------------------------------------------------------- invitations (invitee side)
invites = APIRouter(prefix="/api/invites", tags=["friends"])


def _invite(db: Database, invite_id: int, uid: int) -> dict:
    inv = db.one("SELECT * FROM fade_invites WHERE id=? AND to_user=?", (invite_id, uid))
    if not inv:
        raise HTTPException(404, "Invite not found.")
    return inv


@invites.post("/{invite_id}/accept")
def accept_invite(invite_id: int, body: MemberIn, user: dict = Depends(current_user),
                  db: Database = Depends(db_dep)):
    """The invitee picks one of their own collections and its direction. That is a request: the
    collection joins only once the fade's owner approves it (they may change the direction).
    Asking again replaces the previous request."""
    uid = user["id"]
    inv = _invite(db, invite_id, uid)
    if not are_friends(db, uid, inv["from_user"]):
        db.run("DELETE FROM fade_invites WHERE id=?", (invite_id,))
        raise HTTPException(410, "You're no longer friends.")
    member = _resolve_members(db, uid, [body], create=False)[0]   # checks only, creates nothing yet
    if member["collection_id"] and db.val(
            "SELECT 1 FROM line_members WHERE line_id=? AND account_id=? AND kind=? AND collection_id=?",
            (inv["line_id"], member["account_id"], member["kind"], member["collection_id"])):
        raise HTTPException(409, "This collection is already in the fade.")
    request = {"service": body.service, "kind": member["kind"], "collection_id": member["collection_id"],
               "collection_name": member["collection_name"], "role": member["role"],
               "follow_deletions": bool(member["follow_deletions"]), "create": bool(body.create)}
    db.run("UPDATE fade_invites SET request=?, requested_at=? WHERE id=?",
           (json.dumps(request), time.time(), invite_id))
    fade = db.val("SELECT name FROM lines WHERE id=?", (inv["line_id"],)) or "your fade"
    alert.safely(alert.social, db, inv["from_user"], "fade_request", f"/fades/{inv['line_id']}",
                 name=user["display_name"], fade=fade)
    notify([inv["from_user"]], "fades")
    return overview(db, uid)


@invites.delete("/{invite_id}")
def decline_invite(invite_id: int, user: dict = Depends(current_user), db: Database = Depends(db_dep)):
    """Say no to an invite, or take back a request that is still waiting."""
    inv = _invite(db, invite_id, user["id"])
    db.run("DELETE FROM fade_invites WHERE id=?", (invite_id,))
    notify([inv["from_user"]], "fades")
    return overview(db, user["id"])
