"""Friends and shared fades: who may see a fade, and who is part of it.

Rules (privacy first):
- A fade is visible to its owner and to everyone who has a collection in it. Nobody else ever sees
  its collections, songs or history, friends included.
- A friendship only reveals display name and username. A pending invite only reveals the fade's name,
  icon and colour and who invites.
- A friend's collection joins a fade only when the friend picks it. Before that the owner sees
  nothing of it, not even which services the friend uses.
"""
from __future__ import annotations

from .db import Database

# lines the user owns or takes part in with one of their collections; args: (user_id, user_id)
VISIBLE = ("(l.user_id=? OR l.id IN (SELECT m.line_id FROM line_members m JOIN accounts a ON a.id=m.account_id "
           "WHERE a.user_id=?))")


def visible_line(db: Database, line_id: int, user_id: int) -> dict | None:
    return db.one(f"SELECT l.* FROM lines l WHERE l.id=? AND {VISIBLE}", (line_id, user_id, user_id))


def participants(db: Database, line_id: int) -> set[int]:
    """The owner and everyone with a collection in the fade."""
    rows = db.all("""SELECT user_id FROM lines WHERE id=?
                     UNION SELECT a.user_id FROM line_members m JOIN accounts a ON a.id=m.account_id
                     WHERE m.line_id=?""", (line_id, line_id))
    return {r["user_id"] for r in rows}


def friendship(db: Database, a: int, b: int) -> dict | None:
    return db.one("""SELECT * FROM friendships WHERE (requester_id=? AND addressee_id=?)
                     OR (requester_id=? AND addressee_id=?)""", (a, b, b, a))


def are_friends(db: Database, a: int, b: int) -> bool:
    f = friendship(db, a, b)
    return bool(f and f["status"] == "accepted")


def shared_lines(db: Database, a: int, b: int) -> list[int]:
    """Fades of one of the two that hold a collection of the other."""
    return [r["line_id"] for r in db.all(
        """SELECT DISTINCT m.line_id FROM line_members m JOIN accounts acc ON acc.id=m.account_id
           JOIN lines l ON l.id=m.line_id
           WHERE (l.user_id=? AND acc.user_id=?) OR (l.user_id=? AND acc.user_id=?)""", (a, b, b, a))]


def end_sharing(db: Database, a: int, b: int) -> None:
    """Two people are no longer friends: their collections leave each other's fades and open invites
    between them are dropped."""
    with db.tx() as c:
        c.execute("""DELETE FROM line_members WHERE id IN (
                       SELECT m.id FROM line_members m JOIN accounts acc ON acc.id=m.account_id
                       JOIN lines l ON l.id=m.line_id
                       WHERE (l.user_id=? AND acc.user_id=?) OR (l.user_id=? AND acc.user_id=?))""", (a, b, b, a))
        c.execute("DELETE FROM fade_invites WHERE (from_user=? AND to_user=?) OR (from_user=? AND to_user=?)",
                  (a, b, b, a))
        c.execute("DELETE FROM friendships WHERE (requester_id=? AND addressee_id=?) OR (requester_id=? AND addressee_id=?)",
                  (a, b, b, a))


def notify(user_ids, what: str = "friends") -> None:
    """Tell these people's open apps to reload their friends or fades."""
    from .worker import get_worker
    bus = get_worker().bus
    for uid in set(user_ids):
        bus.publish(uid, {"type": "social", "what": what})


def public_person(u: dict) -> dict:
    """All a friend (or someone invited) learns about a person."""
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"]}
