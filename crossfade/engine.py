"""Sync engine for a "Fade": a group of collections with roles (both / send / receive).

State per line:
  line_items    the set of songs the line considers present (L)
  member_items  per member: songs seen there (confirmed), songs we added but have not seen yet
                (unconfirmed), songs the user removed from a member that does not send (excluded)

One run:
  1. read every member, map tracks to global song ids, merge identical songs across services
  2. senders: song in L, previously seen at the member, now gone   -> removal
              song at the member, not in L                         -> addition
  3. L' = L - removals + additions
  4. receivers get every song of L' they lack; removals are taken out of receivers and of
     send-only members that follow deletions
  5. safety: empty lists and too many removals stop the run / hold removals back

Sorting (sorting.py): a receiver with a rule only gets the songs of L' that fit it; a sender set to
`move_sorted` lets go of a song once it is listed in every collection whose rule it fits. Those
hand-offs count towards the deletion safety limit like removals.

The very first run therefore merges everything (no removals), one-way lines only copy from the
senders, and deletions are only ever derived from songs that were actually seen before.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import history, matcher
from .db import Database
from .identity import Identity
from .models import APPLE, LIKED, SERVICE_NAMES, ApiError, AuthError, Track
from .providers.base import Provider
from .sorting import Sorter

Progress = Callable[[str, int, int], None]
Live = Callable[[str, dict], None]   # (kind, data): results as they happen – start | plan | issue | change
APPLY_CHUNK = 25          # songs per provider call while applying, so results show up as they happen
PENDING_RETRY_AFTER = 3   # runs until an added-but-unseen song is added again
PENDING_GIVE_UP = 9       # after this many runs without seeing it, ask the user instead
GONE_CONFIRM = 600.0      # seconds a song must stay missing before it counts as removed (SLOW_LISTING)
SLOW_LISTING = {APPLE}    # services whose lists drop songs for a while (Apple's favourites, seen live)
RECHECK_DELAY = 3.0       # other services: a second read this much later confirms a removal in the same run
VERIFY_DELAY = 2.0        # seconds before re-reading members that just received songs


def _now() -> float:
    return time.time()


class Stopped(Exception):
    pass


def fill_times(order: list[int], times: dict[int, float]) -> None:
    """Give the songs of a newest-first list that have no time one between their neighbours that
    have one (a second apart where only one side has one). Songs that have a time keep it."""
    anchors = [i for i, sid in enumerate(order) if sid in times]
    if not anchors:
        return
    k = 0
    for i, sid in enumerate(order):
        while k < len(anchors) and anchors[k] < i:
            k += 1
        if sid in times:
            continue
        newer = anchors[k - 1] if k > 0 else None           # above it: liked later
        older = anchors[k] if k < len(anchors) else None    # below it: liked earlier
        if newer is not None and older is not None:
            t_new, t_old = times[order[newer]], times[order[older]]
            if t_new > t_old:
                times[sid] = t_old + (t_new - t_old) * (older - i) / (older - newer)
            else:     # the list doesn't follow the dates here: just above the older one
                times[sid] = t_old + (older - i) * 0.001
        elif older is not None:
            times[sid] = times[order[older]] + (older - i)
        else:
            times[sid] = times[order[newer]] - (i - newer)


@dataclass
class MemberState:
    row: dict
    provider: Provider
    current: dict[int, Track] = field(default_factory=dict)   # song_id -> track as listed now
    pos: dict[int, int] = field(default_factory=dict)         # song_id -> first place in the collection
    extra: dict[int, list[Track]] = field(default_factory=dict)  # further entries of the same song (video + song)
    copies: dict[str, int] = field(default_factory=dict)       # ref -> how often the collection lists it
    known: dict[int, dict] = field(default_factory=dict)      # song_id -> member_items row
    to_add: dict[int, Track] = field(default_factory=dict)    # song_id -> target track
    to_remove: dict[int, Track] = field(default_factory=dict)
    pending: set[int] = field(default_factory=set)
    gave_up: set[int] = field(default_factory=set)
    excluded_now: set[int] = field(default_factory=set)
    added_ok: set[int] = field(default_factory=set)
    seen_after_add: set[int] = field(default_factory=set)     # added this run and already listed
    add_notes: dict[int, str] = field(default_factory=dict)     # song -> "different version: …"
    remove_notes: dict[int, str] = field(default_factory=dict)  # song -> "sorted into …"
    removed_ok: set[int] = field(default_factory=set)
    vanishing: dict[int, float] = field(default_factory=dict)  # song -> missing since (not confirmed yet)
    missing_twice: set[int] = field(default_factory=set)       # missing on two reads of this run
    error: str | None = None

    @property
    def id(self) -> int:
        return self.row["id"]

    @property
    def sends(self) -> bool:
        return self.row["role"] in ("both", "send")

    @property
    def receives(self) -> bool:
        return self.row["role"] in ("both", "receive")

    @property
    def hands_off(self) -> bool:
        """Lets go of songs once they are sorted into the fade's rule collections."""
        return self.sends and bool(self.row.get("move_sorted"))

    @property
    def takes_removals(self) -> bool:
        """Removals elsewhere are applied here: receivers, and senders set to follow deletions."""
        return self.receives or bool(self.row.get("follow_deletions"))

    @property
    def service(self) -> str:
        return self.row["service"]

    def label(self) -> str:
        return f"{SERVICE_NAMES[self.service]} · {self.row['collection_name']}"


@dataclass
class RunResult:
    status: str = "ok"                 # ok | partial | needs_confirm | blocked | auth_error | error | preview
    message: str | None = None
    summary: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)


class LineSync:
    def __init__(self, db: Database, line_id: int, providers: dict[int, Provider],
                 progress: Progress | None = None, should_stop: Callable[[], bool] | None = None,
                 run_id: int | None = None, live: Live | None = None):
        self.db = db
        self.live = live or (lambda kind, data: None)
        self.run_id = run_id
        self.ident = Identity(db)
        self.line = db.one("SELECT * FROM lines WHERE id=?", (line_id,))
        if not self.line:
            raise ValueError("Fade not found")
        rows = db.all("""SELECT m.*, a.service, a.yt_format, a.user_id FROM line_members m
                         JOIN accounts a ON a.id=m.account_id WHERE m.line_id=? ORDER BY m.position, m.id""",
                      (line_id,))
        self.members = [MemberState(r, providers[r["account_id"]]) for r in rows]
        self.sorter = Sorter(db, rows)
        self.unsorted: set[int] = set()     # songs a rule can't place yet: they need labels first
        self.moving: dict[int, set[int]] = {}   # member -> songs it lets go of, now that they are sorted
        self.progress = progress or (lambda *a: None)
        self.should_stop = should_stop or (lambda: False)
        self.L: set[int] = set()            # line set before this run
        self.new_L: set[int] = set()        # line set after this run
        self.added: set[int] = set()
        self.removed: dict[int, MemberState] = {}   # song -> member where the user removed it
        self.held: set[int] = set()         # removals waiting for confirmation
        self.policy = self.line.get("version_policy") or "closest"
        self._like_times: dict[int, float] | None = None   # see like_times
        # the account being talked to: an auth error belongs to it (a shared fade can hold two
        # accounts of the same service, one per person)
        self.active_account: int | None = None

    def _check_stop(self):
        if self.should_stop():
            raise Stopped()

    # ================================================================ read
    def _read(self) -> None:
        for i, m in enumerate(self.members, 1):
            self._check_stop()
            self.progress(f"Reading {m.label()} …", i - 1, len(self.members))
            self.active_account = m.row["account_id"]
            tracks = m.provider.read(m.row["kind"], m.row["collection_id"])
            for n, t in enumerate(tracks):
                sid = self.ident.song_for(t)
                m.pos.setdefault(sid, n)
                m.copies[t.id] = t.handle.get("copies", 1)
                if m.current.setdefault(sid, t) is not t:
                    m.extra.setdefault(sid, []).append(t)
            m.known = {r["song_id"]: r for r in self.db.all("SELECT * FROM member_items WHERE member_id=?", (m.id,))}
            history.capture(self.db, m.row["account_id"], m.row["kind"], m.row["collection_id"] or "",
                            [t.id for t in tracks], "sync")

    def _read_again(self, L: set[int]) -> None:
        """A song of the fade that a member listed before is missing now. Outside SLOW_LISTING one
        more read a few seconds later settles it within this run: still missing -> the user removed
        it; listed again -> the first read missed it, nothing happens. If that read fails, the
        removal waits for GONE_CONFIRM like everywhere else."""
        if GONE_CONFIRM <= 0:
            return      # removals count at once anyway
        todo = []
        for m in self.members:
            if m.service in SLOW_LISTING:
                continue
            missing = {sid for sid in L if sid not in m.current
                       and (k := m.known.get(sid)) and k["confirmed"] and not k["excluded"]}
            if missing:
                todo.append((m, missing))
        if not todo:
            return
        time.sleep(RECHECK_DELAY)
        for i, (m, missing) in enumerate(todo):
            self._check_stop()
            self.progress(f"Checking {m.label()} once more …", i, len(todo))
            self.active_account = m.row["account_id"]
            try:
                tracks = m.provider.read(m.row["kind"], m.row["collection_id"])
            except (AuthError, ApiError):
                continue
            listed: dict[int, tuple[int, Track]] = {}
            for n, t in enumerate(tracks):
                listed.setdefault(self.ident.song_for(t), (n, t))
            for sid in missing:
                if sid in listed:
                    n, t = listed[sid]
                    m.current[sid] = t
                    m.pos.setdefault(sid, n)
                    m.copies[t.id] = t.handle.get("copies", 1)
                else:
                    m.missing_twice.add(sid)

    def _merge_across_services(self) -> None:
        """Identify the same song across members of different services before any search."""
        services = {m.service for m in self.members}
        if len(services) < 2:
            return
        for a_svc in services:
            for b_svc in services:
                if a_svc >= b_svc:
                    continue
                left = self._unlinked(a_svc, b_svc)
                right = self._unlinked(b_svc, a_svc)
                lids = {id(v): k for k, v in left.items()}
                rids = {id(v): k for k, v in right.items()}
                for lt, rt, _ in matcher.pair_lists(list(left.values()), list(right.values()),
                                                    require_exact=self.policy == "exact"):
                    keep, drop = lids[id(lt)], rids[id(rt)]
                    self.ident.merge(keep, drop)
                    self._remap(drop, keep)

    def _unlinked(self, svc: str, other: str) -> dict[int, Track]:
        """Songs present at members of `svc` that have no ref on `other` yet."""
        out: dict[int, Track] = {}
        for m in self.members:
            if m.service != svc:
                continue
            for sid, t in m.current.items():
                if sid not in out and not self.db.val(
                        "SELECT 1 FROM song_refs WHERE song_id=? AND service=?", (sid, other)):
                    out[sid] = t
        return out

    def _remap(self, old: int, new: int) -> None:
        """After merging song `old` into `new`, update all in-memory state of this run."""
        for m in self.members:
            if old in m.current and new in m.current:      # both entries are listed: one song twice
                m.extra.setdefault(new, []).append(m.current[old])
            if old in m.extra:
                m.extra.setdefault(new, []).extend(m.extra.pop(old))
            if old in m.pos:
                p = m.pos.pop(old)
                m.pos[new] = min(p, m.pos.get(new, p))
            for d in (m.current, m.known, m.to_add, m.to_remove, m.vanishing):
                if old in d:
                    v = d.pop(old)
                    d.setdefault(new, v)
            for s in (m.pending, m.excluded_now, m.missing_twice):
                if old in s:
                    s.discard(old)
                    s.add(new)
            if new in m.current:          # merged song is already there
                m.to_add.pop(new, None)
                m.pending.discard(new)
        for s in (self.L, self.new_L, self.held, self.added):
            if old in s:
                s.discard(old)
                s.add(new)
        if self._like_times and old in self._like_times:
            at = self._like_times.pop(old)
            self._like_times[new] = min(at, self._like_times.get(new, at))
        if old in self.removed:
            self.removed.setdefault(new, self.removed.pop(old))

    def _order(self, sid: int) -> tuple:
        """Where a song stands in the fade's sources: songs are added in that order, so a receiving
        collection is built like its sources. Liked songs of every service go on one timeline, by
        when they were liked (like_times); other songs follow the first sending member (in the
        fade's order) that lists them; songs no sender lists any more come last.

        Liked songs are listed newest first by every service, but adding can only append (or, for
        liked songs, stamp "liked now"): so they go in oldest first. A playlist then reads oldest to
        newest like later additions do, and liked songs elsewhere show the newest on top again."""
        for rank, m in enumerate(self.members):
            if m.sends and sid in m.pos:
                if m.row["kind"] == LIKED and sid in self.like_times:
                    return (0, self.like_times[sid], 0, sid)
                return (1 + rank, 0, -m.pos[sid] if m.row["kind"] == LIKED else m.pos[sid], sid)
        return (len(self.members) + 1, 0, 0, sid)

    @property
    def like_times(self) -> dict[int, float]:
        if self._like_times is None:
            self._like_times = self._compute_like_times()
        return self._like_times

    def _compute_like_times(self) -> dict[int, float]:
        """When each song was liked, as far as the services say (Spotify and YouTube do, Apple Music
        doesn't): the earliest date of an entry the person made, not one Crossfade added, ever seen
        in this fade (kept in like_times: a song liked again is dated anew). A song without one gets
        a time between its neighbours in a liked list where the person put it; only then does the
        date Crossfade added it somewhere count."""
        line_id = self.line["id"]
        ours = {(r["member_id"], r["song_id"]) for r in self.db.all(
            "SELECT DISTINCT member_id, song_id FROM changes WHERE line_id=? AND action='add' AND ok=1", (line_id,))}
        lists = [m for m in self.members if m.sends and m.row["kind"] == LIKED]
        own = {r["song_id"]: r["liked_at"] for r in self.db.all(
            "SELECT song_id, liked_at FROM like_times WHERE line_id=?", (line_id,))}
        known = dict(own)
        added: dict[int, float] = {}
        for m in lists:
            for sid, t in m.current.items():
                at = t.handle.get("added_at")
                if at is not None:
                    d = added if (m.id, sid) in ours else own
                    d[sid] = min(d.get(sid, at), at)
        fresh = [(line_id, sid, at) for sid, at in own.items() if known.get(sid) != at]
        if fresh:
            with self.db.tx() as c:
                c.executemany("""INSERT INTO like_times(line_id,song_id,liked_at) VALUES(?,?,?)
                                 ON CONFLICT(line_id,song_id) DO UPDATE SET liked_at=excluded.liked_at""", fresh)
        times = dict(own)
        for m in lists:
            fill_times([sid for sid in sorted(m.pos, key=m.pos.get) if (m.id, sid) not in ours], times)
        for sid, at in added.items():
            times.setdefault(sid, at)
        return times

    # ================================================================ plan
    def run(self, apply: bool, allow_deletions: bool = False) -> RunResult:
        line = self.line
        self.live("start", {"mode": "apply" if apply else "preview", "members": [{
            "member_id": m.id, "service": m.service, "name": m.row["collection_name"], "role": m.row["role"],
            "follow_deletions": bool(m.row.get("follow_deletions")), "count": 0} for m in self.members]})
        try:
            self._read()
        except AuthError as e:
            return RunResult("auth_error", str(e))
        except ApiError as e:   # includes NotSupported
            return RunResult("error", str(e))
        self._check_stop()
        self.L = {r["song_id"] for r in self.db.all("SELECT song_id FROM line_items WHERE line_id=?", (line["id"],))}
        self._merge_across_services()

        # -- safety: an empty list where songs were seen before is an API/auth problem
        # (a confirmed run – allow_deletions – means the user checked: the list really is empty)
        for m in self.members:
            confirmed = sum(1 for r in m.known.values() if r["confirmed"])
            if not m.current and confirmed >= 3 and not allow_deletions:
                return RunResult("blocked", f"{m.label()} came back empty although {confirmed} songs "
                                            "should be there. To be safe, nothing was changed. If the collection really "
                                            "is empty, confirm the sync.")

        L = self.L
        self._read_again(L)
        now = _now()
        for m in self.members:
            confirmed = {sid for sid, r in m.known.items() if r["confirmed"] and not r["excluded"]}
            # Apple's favourites drop a just-added song for a while: there a song only counts as
            # removed once it was missing on two reads at least GONE_CONFIRM apart. Elsewhere a
            # second read a few seconds later is enough (_read_again). Until then it is left alone
            # everywhere.
            gone = set()
            for sid in L:
                if sid in confirmed and sid not in m.current:
                    since = m.known[sid]["missing_since"]
                    if GONE_CONFIRM <= 0 or sid in m.missing_twice or (
                            since is not None and now - since >= GONE_CONFIRM):
                        gone.add(sid)
                    else:
                        m.vanishing[sid] = since if since is not None else now
            if m.sends:
                for sid in m.current:
                    if sid not in L and not (m.known.get(sid) or {}).get("excluded"):
                        self.added.add(sid)
                if line["sync_deletions"]:
                    for sid in gone:
                        self.removed.setdefault(sid, m)
                else:
                    m.excluded_now |= gone          # keep it out of this member, keep it elsewhere
            else:
                m.excluded_now |= gone              # receive-only: the user's removal is respected

        # -- a source that only sends and does not follow deletions cannot be changed by the line;
        #    while it still has a song, a removal elsewhere only removes it there (otherwise it
        #    would come back every run)
        for sid in list(self.removed):
            src = self.removed[sid]
            if any(o is not src and o.sends and not o.takes_removals and sid in o.current for o in self.members):
                del self.removed[sid]
                if src.receives:
                    src.excluded_now.add(sid)

        # -- removals above the safety limit are held back until confirmed
        removal_targets = sum(1 for sid, src in self.removed.items() for m in self.members
                              if m.takes_removals and m is not src and sid in m.current)
        # delete_confirm off: the user never wants to be asked, however many songs go at once
        limit = (max(int(line["delete_guard_min"]), int(line["delete_guard_ratio"] * max(len(L), 1)))
                 if line["delete_confirm"] else float("inf"))
        needs_confirm = bool(self.removed) and removal_targets > limit and not allow_deletions
        if needs_confirm:
            self.held = set(self.removed)
            self.removed = {}

        self.new_L = (L - set(self.removed)) | self.added
        if self.sorter.active:
            self.sorter.load(self.new_L)
        ignored = {(r["member_id"], r["song_id"]) for r in self.db.all(
            "SELECT member_id, song_id FROM issues WHERE line_id=? AND kind='ignored'", (line["id"],))}

        # -- what each receiver needs (and what each member following deletions loses)
        for m in self.members:
            if m.takes_removals:
                for sid, src in self.removed.items():
                    if sid in m.current and src is not m:
                        m.to_remove[sid] = m.current[sid]
            if not m.receives:
                continue
            for sid in sorted(self.new_L, key=self._order):
                if sid in m.current or sid in self.held or sid in m.vanishing:
                    continue
                if self.sorter.has_rule(m.id):
                    fit = self.sorter.fits(m.id, sid)
                    if fit is None:
                        self.unsorted.add(sid)
                    if fit is not True:
                        continue
                k = m.known.get(sid)
                if sid in m.excluded_now or (k and k["excluded"]):
                    continue
                if k and not k["confirmed"]:
                    if k["misses"] >= PENDING_GIVE_UP:
                        m.gave_up.add(sid)
                        continue
                    if (k["misses"] + 1) % PENDING_RETRY_AFTER:
                        m.pending.add(sid)        # wait – services take a while to list new items
                        continue
                    # every few runs without seeing it: add it once more
                m.to_add[sid] = None  # type: ignore[assignment]  # resolved below

        # -- sorted songs leave their inbox (e.g. liked songs); many at once wait like removals do
        self._plan_moves(ignored)
        moving = sum(len(v) for v in self.moving.values())
        if moving > limit and not allow_deletions:
            for m in self.members:
                for sid in self.moving.get(m.id, ()):
                    m.to_remove.pop(sid, None)
                    m.remove_notes.pop(sid, None)
                    self.held.add(sid)
            self.moving = {}
            needs_confirm = True

        # -- known right away: removals, held removals, songs still on their way
        for sid in self.held:
            self.live("plan", {"group": "held", "track": self._track_info(None, sid)})
        for m in self.members:
            for sid, tr in m.to_remove.items():
                self.live("plan", {"member_id": m.id, "group": "remove", "track": self._track_info(tr, sid)})
            for sid in m.pending:
                self.live("plan", {"member_id": m.id, "group": "pending", "track": self._track_info(None, sid)})

        # -- find the songs on the receivers' services
        unmatched: list[dict] = []
        total = sum(len(m.to_add) for m in self.members)
        i = 0
        deferred: set[tuple[int, int]] = set()    # missed once, waiting for the other services' results
        while True:
            todo = [(m, sid) for m in self.members for sid, t in m.to_add.items() if t is None]
            if not todo:
                break
            m, sid = next(((m, s) for m, s in todo if (m.id, s) not in deferred), todo[0])
            retry = (m.id, sid) in deferred
            deferred.discard((m.id, sid))
            i += not retry
            self._check_stop()
            if (m.id, sid) in ignored:
                m.to_add.pop(sid, None)
                continue
            origin = self._origin(sid)
            desc = self.ident.describe(sid, origin)
            self.progress(f"Searching {SERVICE_NAMES[m.service]} for “{desc.label()}” …", min(i, total), total)
            self.active_account = m.row["account_id"]
            try:
                match = self.ident.find(sid, m.service, m.provider, source=desc)
            except AuthError as e:
                return RunResult("auth_error", str(e))
            if match.accepted(self.policy):
                dropped = self.ident.link(sid, match.track)
                if dropped is not None:
                    self._remap(dropped, sid)
                if sid in m.current or sid not in m.to_add:
                    m.to_add.pop(sid, None)
                    continue      # turned out to be there already under another identity
                # YouTube: the music video or the audio track, as the user prefers
                target, dropped = self.ident.in_format(sid, match.track, m.provider, m.row.get("yt_format"))
                if dropped is not None:
                    self._remap(dropped, sid)
                if sid in m.current or sid not in m.to_add:
                    m.to_add.pop(sid, None)
                    continue
                m.to_add[sid] = target
                if match.note():
                    m.add_notes[sid] = match.note()
                self.live("plan", {"member_id": m.id, "group": "add",
                                   "track": self._track_info(target, sid, m.add_notes.get(sid))})
            elif not retry and not match.exact and any(
                    o is not m and o.service not in (m.service, desc.service) and o.to_add.get(sid, 1) is None
                    for o in self.members):
                # another service still has to find this song: its result may help here (a YouTube title
                # found on Spotify, not on Apple Music -> search Apple Music with Spotify's details)
                deferred.add((m.id, sid))
            else:
                m.to_add.pop(sid, None)
                reason = None
                if match.track and match.score >= matcher.ACCEPT and not match.exact:
                    reason = ("Only found under a different artist name" if matcher.RENAMED in match.notes else
                              "Only found in a different version: " + ", ".join(match.notes or ["different"]))
                unmatched.append({"member_id": m.id, "song_id": sid, "track": desc.to_dict(),
                                  "candidates": match.candidates, "reason": reason})
                self.live("issue", unmatched[-1])

        for m in self.members:
            for sid in m.gave_up:
                d = self.ident.describe(sid).to_dict()
                unmatched.append({"member_id": m.id, "song_id": sid, "track": d, "candidates": [],
                                  "reason": f"Was added to {SERVICE_NAMES[m.service]} several times but never shows up "
                                            "there. Maybe the song isn't available in your country."})
                self.live("issue", unmatched[-1])

        res = RunResult()
        res.plan = self._plan_dict(unmatched)
        res.summary = res.plan["summary"]
        if not apply:
            res.status = "needs_confirm" if needs_confirm else "preview"
            if needs_confirm:
                res.message = f"{len(self.held)} deletions are waiting for your confirmation."
            self._store_unmatched(unmatched)
            return res

        # ================================================================ apply
        total = sum(len(m.to_add) + len(m.to_remove) for m in self.members)
        done = failures = 0
        changes: list[tuple] = []
        abort: tuple[str, str] | None = None
        for m in self.members:
            for action, items in (("remove", m.to_remove), ("add", m.to_add)):
                pending_items = list(items.items())
                if action == "add":       # keep the sources' order (merges during the search can shuffle it)
                    pending_items.sort(key=lambda it: self._order(it[0]))
                verb, prep = ("Removing", "from") if action == "remove" else ("Adding", "to")
                fn = m.provider.remove if action == "remove" else m.provider.add
                ok_set = m.removed_ok if action == "remove" else m.added_ok
                for start in range(0, len(pending_items), APPLY_CHUNK):
                    if abort:
                        break
                    if self.should_stop():
                        abort = ("stopped", "Stopped")
                        break
                    part = pending_items[start:start + APPLY_CHUNK]
                    self.progress(f"{verb} {len(items)} songs {prep} {m.label()} …", done, total)
                    tracks = [t for _, t in part]
                    self.active_account = m.row["account_id"]
                    if action == "remove":     # every entry of the song, not just the first
                        tracks += [x for sid, _ in part for x in m.extra.get(sid, [])]
                    try:
                        failed = set(fn(m.row["kind"], m.row["collection_id"], tracks))
                    except AuthError as e:
                        abort = ("auth_error", str(e))
                        break
                    except ApiError as e:
                        failed = {t.id for t in tracks}
                        m.error = str(e)
                    for sid, t in part:
                        ok = t.id not in failed
                        if ok:
                            ok_set.add(sid)
                        else:
                            failures += 1
                        ch = (m.id, sid, action, t.label(), int(ok),
                              None if ok else (m.error or "failed"), time.time(),
                              (m.add_notes if action == "add" else m.remove_notes).get(sid))
                        changes.append(ch)
                        self.live("change", {
                            "run_id": self.run_id, "member_id": m.id, "song_id": sid, "action": action,
                            "label": ch[3], "ok": ch[4], "message": ch[5], "ts": ch[6], "note": ch[7],
                            "artwork": t.artwork, "service": m.service, "collection_name": m.row["collection_name"]})
                    done += len(part)
                if abort:
                    break
            if abort:
                break
        self.progress("Checking results …", done, total)

        if not abort:
            self._verify_added()
        self._commit(changes, unmatched)
        res.summary["failed"] = failures
        if abort:
            res.status, res.message = abort
        elif needs_confirm:
            res.status, res.message = "needs_confirm", f"{len(self.held)} deletions are waiting for your confirmation."
        elif failures:
            res.status, res.message = "partial", f"{failures} changes didn't go through. Crossfade will try again on the next sync."
        else:
            res.status = "ok"
        return res

    def _plan_moves(self, ignored: set[tuple[int, int]]) -> None:
        """A song leaves a hand-off sender once it is listed in some collection whose rule it fits
        and every other such collection has it too or won't get it (the user removed or ignored it
        there, or the service never shows it). Never before it has arrived anywhere: nothing is lost."""
        targets = [m for m in self.members if self.sorter.has_rule(m.id)]
        for src in self.members:
            if not src.hands_off or not targets:
                continue
            for sid, track in src.current.items():
                if sid in src.to_remove or sid in self.removed or sid in self.held:
                    continue
                fitting = [m for m in targets if m is not src and self.sorter.fits(m.id, sid) is True]
                arrived = [m for m in fitting if sid in m.current]
                if not arrived:
                    continue

                if all(self._settled(m, sid, ignored) for m in fitting):
                    src.to_remove[sid] = track
                    src.remove_notes[sid] = "sorted into " + ", ".join(m.row["collection_name"] for m in arrived)
                    self.moving.setdefault(src.id, set()).add(sid)

    @staticmethod
    def _settled(m: MemberState, sid: int, ignored: set[tuple[int, int]]) -> bool:
        """The song is listed at this member, or won't ever be (removed there by the user, ignored,
        never showing up on the service)."""
        k = m.known.get(sid)
        return (sid in m.current or (m.id, sid) in ignored or sid in m.excluded_now
                or sid in m.gave_up or bool(k and k["excluded"]))

    def _origin(self, sid: int) -> Track | None:
        """The track a song came from (a sending member that has it) – its version is the one to find.
        YouTube music videos are replaced by their audio version (the "song" YouTube Music links)."""
        for m in self.members:
            if m.sends and sid in m.current:
                t = m.current[sid]
                if t.video_type in ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC") and hasattr(m.provider, "audio_version"):
                    try:
                        audio = m.provider.audio_version(t.id)
                    except Exception:  # noqa: BLE001 - best effort
                        audio = None
                    if audio:
                        dropped = self.ident.link(sid, audio)
                        if dropped is not None:
                            self._remap(dropped, sid)
                        return audio
                return t
        return None

    def _verify_added(self) -> None:
        """Read every member that just received songs once more. Songs already listed count as
        confirmed right away, so if the user removes one before the next run, that run sees a
        removal (and passes it on) instead of a song that has "not arrived yet" (and re-adding it).
        Songs not listed yet stay pending as before – we never guess that a song was removed."""
        todo = [m for m in self.members if m.added_ok]
        if not todo:
            return
        time.sleep(VERIFY_DELAY)
        for i, m in enumerate(todo):
            if self.should_stop():
                return
            self.progress(f"Checking {m.label()} …", i, len(todo))
            try:
                tracks = m.provider.read(m.row["kind"], m.row["collection_id"])
            except (AuthError, ApiError):
                continue          # not fatal: they stay pending and are confirmed on a later run
            listed = {self.ident.song_for(t) for t in tracks}
            m.seen_after_add = m.added_ok & listed

    # ================================================================ persist
    def _commit(self, changes: list[tuple], unmatched: list[dict]) -> None:
        db, line_id, now = self.db, self.line["id"], time.time()
        # Removals that failed or were not attempted stay in the line so they are retried.
        retry = {sid for m in self.members for sid in m.to_remove if sid not in m.removed_ok}
        keep = self.held | retry
        final_L = self.new_L | keep
        with db.tx() as c:
            c.execute("DELETE FROM line_items WHERE line_id=?", (line_id,))
            c.executemany("INSERT OR IGNORE INTO line_items(line_id,song_id,added_at) VALUES(?,?,?)",
                          [(line_id, sid, now) for sid in final_L])
            # a song that left the fade and is liked again later is a new like
            c.execute("DELETE FROM like_times WHERE line_id=? AND song_id NOT IN "
                      "(SELECT song_id FROM line_items WHERE line_id=?)", (line_id, line_id))
            for m in self.members:
                rows = []
                for sid in final_L:
                    k = m.known.get(sid)
                    if sid in m.removed_ok:
                        # sorted out of a collection that also receives: it must not come back there
                        if m.receives and sid in self.moving.get(m.id, ()):
                            rows.append((m.id, sid, None, 0, 1, 0, None))
                        continue
                    if sid in m.current:
                        rows.append((m.id, sid, m.current[sid].id, 1, 0, 0, None))
                    elif sid in m.vanishing and k:
                        rows.append((m.id, sid, k["ref_id"], 1, 0, k["misses"], m.vanishing[sid]))
                    elif sid in m.excluded_now or (k and k["excluded"]):
                        rows.append((m.id, sid, k["ref_id"] if k else None, 0, 1, 0, None))
                    elif sid in m.seen_after_add:
                        rows.append((m.id, sid, m.to_add[sid].id, 1, 0, 0, None))
                    elif sid in m.added_ok:
                        # keep counting runs without seeing it (reset only when it shows up)
                        rows.append((m.id, sid, m.to_add[sid].id, 0, 0, (k["misses"] + 1) if k else 0, None))
                    elif sid in m.gave_up and k:
                        rows.append((m.id, sid, k["ref_id"], 0, 0, k["misses"], None))
                    elif sid in m.pending and k:
                        rows.append((m.id, sid, k["ref_id"], 0, 0, k["misses"] + 1, None))
                    elif sid in keep and k:
                        rows.append((m.id, sid, k["ref_id"], k["confirmed"], 0, k["misses"], None))
                c.execute("DELETE FROM member_items WHERE member_id=?", (m.id,))
                c.executemany("INSERT INTO member_items(member_id,song_id,ref_id,confirmed,excluded,misses,"
                              "missing_since) VALUES(?,?,?,?,?,?,?)", rows)
                count = len(m.current) + len(m.added_ok) - len(m.removed_ok)
                c.execute("UPDATE line_members SET initialized=1, item_count=? WHERE id=?", (count, m.id))
            c.executemany("INSERT INTO changes(run_id,line_id,member_id,song_id,action,label,ok,message,ts,note) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", [(self.run_id, line_id, *ch) for ch in changes])
        self._store_unmatched(unmatched)
        self._update_catalog()

    def _update_catalog(self) -> None:
        """The run just read these collections and changed them: bring the matching music-catalog
        sources up to date, so the library does not show what was true at its last scan."""
        now = time.time()
        for m in self.members:
            src = self.db.one("SELECT id FROM catalog_sources WHERE account_id=? AND kind=? AND collection_id=?",
                              (m.row["account_id"], m.row["kind"], m.row["collection_id"] or ""))
            if not src:
                continue
            # every listed entry (a song can be there twice, as video and as song), under its current song
            refs: dict[str, int] = {}
            for sid, t in m.current.items():
                if sid not in m.removed_ok:
                    for x in [t, *m.extra.get(sid, [])]:
                        refs[x.id] = sid
            for sid in m.added_ok:
                tr = m.to_add.get(sid)
                if tr is not None:
                    refs.setdefault(tr.id, sid)
            with self.db.tx() as c:
                c.execute("CREATE TEMP TABLE IF NOT EXISTS _cat_keep(ref_id TEXT PRIMARY KEY)")
                c.execute("DELETE FROM _cat_keep")
                c.executemany("INSERT OR IGNORE INTO _cat_keep(ref_id) VALUES(?)", [(r,) for r in refs])
                c.execute("DELETE FROM catalog_items WHERE source_id=? AND ref_id NOT IN (SELECT ref_id FROM _cat_keep)",
                          (src["id"],))
                c.executemany("""INSERT INTO catalog_items(source_id,ref_id,song_id,position,first_seen,copies)
                                 VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,ref_id) DO UPDATE SET song_id=excluded.song_id,
                                 copies=excluded.copies""",
                              [(src["id"], ref, sid, 1_000_000 + i, now, m.copies.get(ref, 1))
                               for i, (ref, sid) in enumerate(refs.items())])
                c.execute("UPDATE catalog_sources SET item_count=(SELECT COUNT(*) FROM catalog_items WHERE source_id=?), "
                          "scanned_at=?, error=NULL WHERE id=?", (src["id"], now, src["id"]))

    def _store_unmatched(self, unmatched: list[dict]) -> None:
        db, line_id = self.db, self.line["id"]
        with db.tx() as c:
            c.execute("DELETE FROM issues WHERE line_id=? AND kind='unmatched'", (line_id,))
            c.executemany("INSERT OR IGNORE INTO issues(line_id,member_id,song_id,kind,data,created_at) "
                          "VALUES(?,?,?,'unmatched',?,?)",
                          [(line_id, u["member_id"], u["song_id"],
                            json.dumps({"track": u["track"], "candidates": u["candidates"],
                                        "reason": u.get("reason")}), time.time())
                           for u in unmatched])

    def _track_info(self, track: Track | None, sid: int, note: str | None = None) -> dict:
        d = (track or self.ident.describe(sid)).to_dict()
        d["song_id"] = sid
        if note:
            d["note"] = note
        return d

    def _plan_dict(self, unmatched: list[dict]) -> dict:
        t = self._track_info

        members = [{
            "member_id": m.id, "service": m.service, "name": m.row["collection_name"], "role": m.row["role"],
            "follow_deletions": bool(m.row.get("follow_deletions")), "count": len(m.current),
            "add": [t(tr, sid, m.add_notes.get(sid)) for sid, tr in m.to_add.items()],
            "remove": [t(tr, sid, m.remove_notes.get(sid)) for sid, tr in m.to_remove.items()],
            "pending": [t(None, sid) for sid in m.pending],
        } for m in self.members]
        # removed at a sender but not confirmed yet (GONE_CONFIRM): goes everywhere on a later run
        waiting = {sid: since for m in self.members if m.sends and self.line["sync_deletions"]
                   for sid, since in m.vanishing.items()}
        summary = {
            "songs": len(self.new_L | self.held),
            "add": sum(len(m["add"]) for m in members),
            "remove": sum(len(m["remove"]) for m in members),
            "new_songs": len(self.added), "removed_songs": len(self.removed), "held": len(self.held),
            "unmatched": len(unmatched), "pending": sum(len(m["pending"]) for m in members),
            "approximate": sum(len(m.add_notes) for m in self.members),
            "sorted_out": sum(len(v) for v in self.moving.values()),
            "unsorted": len(self.unsorted),
            "vanishing": len(waiting),
        }
        if waiting:     # the scheduler runs the fade again then, whatever its interval
            summary["recheck_at"] = min(waiting.values()) + GONE_CONFIRM + 5
        return {"summary": summary, "members": members, "held": [t(None, sid) for sid in self.held],
                "unmatched": [{"member_id": u["member_id"], "song_id": u["song_id"], **u["track"]} for u in unmatched]}

    # ================================================================ order of liked songs
    def reorder(self, apply: bool, member_ids: set[int] | None = None) -> RunResult:
        """Bring liked collections into the order the songs were liked (like_times). Services only
        put a liked song on top (by liking it again), so every song out of place is unliked and liked
        again, one by one, oldest first; the songs already in order at the bottom stay where they are.

        Never loses a song: it is liked again right after (with retries); if that fails the run stops
        and the next sync adds it again. Songs a service doesn't list right after are marked as on
        their way, so a sync doesn't take them for removed."""
        try:
            self._read()
        except AuthError as e:
            return RunResult("auth_error", str(e))
        except ApiError as e:
            return RunResult("error", str(e))
        self._check_stop()
        self._merge_across_services()
        targets = [m for m in self.members
                   if m.row["kind"] == LIKED and (member_ids is None or m.id in member_ids)]
        moves = {m.id: self._moves(m) for m in targets}
        rows = [{"member_id": m.id, "service": m.service, "name": m.row["collection_name"],
                 "count": len(m.current), "moves": len(moves[m.id])} for m in targets]
        res = RunResult("preview", summary={"reorder": rows})
        if not apply:
            return res

        total = sum(len(v) for v in moves.values())
        done = moved = failed = 0
        lost: list[tuple[MemberState, int]] = []
        stop: tuple[str, str] | None = None
        for m in targets:
            kind, cid = m.row["kind"], m.row["collection_id"]
            self.active_account = m.row["account_id"]
            done_here: set[int] = set()
            for sid in moves[m.id]:
                if self.should_stop():
                    stop = ("stopped", "Stopped")
                    break
                self.progress(f"Putting {m.label()} in order …", done, total)
                entries = [m.current[sid], *m.extra.get(sid, [])]
                done += 1
                try:
                    not_removed = set(m.provider.remove(kind, cid, entries))
                except AuthError as e:
                    stop = ("auth_error", str(e))
                    break
                except ApiError:
                    not_removed = set()      # may have gone through in part: like it again below
                if not_removed >= {t.id for t in entries}:
                    failed += 1              # still liked where it was
                    continue
                ok, err = self._like_again(m, entries)
                if not ok:
                    lost.append((m, sid))
                    stop = ("auth_error", err) if err else ("error", f"Couldn't like a song in {m.label()} "
                                                                     "again. The next sync adds it again.")
                    break
                moved += 1
                done_here.add(sid)
            self._settle_moved(m, done_here, [sid for x, sid in lost if x is m])
            if stop:
                break
        res.summary.update(moved=moved, failed=failed, lost=len(lost))
        if stop:
            res.status, res.message = stop
        elif failed:
            res.status, res.message = "partial", f"{failed} songs stayed where they were."
        else:
            res.status = "ok"
        return res

    REORDER_GAP = 1.1      # seconds between two songs liked again: services keep the time to the second
    _last_like = 0.0

    def _like_again(self, m: MemberState, entries: list[Track]) -> tuple[bool, str | None]:
        """Like the entries again (a few tries). (ok, auth error message)."""
        for attempt in range(3):
            wait = self.REORDER_GAP - (time.monotonic() - self._last_like)
            if wait > 0:
                time.sleep(wait)
            self._last_like = time.monotonic()
            try:
                if not m.provider.add(m.row["kind"], m.row["collection_id"], entries):
                    return True, None
            except AuthError as e:
                return False, str(e)
            except ApiError:
                pass
            time.sleep(2 * (attempt + 1))
        return False, None

    def _moves(self, m: MemberState) -> list[int]:
        """The songs to like again at `m`, oldest first, so the list ends up newest first by
        like_times. Songs without a time keep their place among their neighbours; songs with the
        same time keep their order."""
        listed = sorted(m.pos, key=m.pos.get)                 # as listed now: newest first
        times = dict(self.like_times)
        fill_times(listed, times)
        if len(times.keys() & set(listed)) < len(listed):      # nothing to go by at all
            return []
        target = sorted(listed, key=lambda sid: (-times[sid], m.pos[sid]))
        keep, above = 0, None
        for sid in reversed(target):      # the oldest songs that are in order already stay below
            if above is not None and m.pos[sid] >= m.pos[above]:
                break
            keep, above = keep + 1, sid
        return list(reversed(target[:len(target) - keep]))

    def _settle_moved(self, m: MemberState, moved: set[int], lost: list[int]) -> None:
        """After liking songs again: one the service doesn't list yet counts as on its way (not as
        removed by the person); one that couldn't be liked again is added by the next sync."""
        unseen: set[int] = set()
        if moved:
            time.sleep(VERIFY_DELAY)
            try:
                listed = {self.ident.song_for(t) for t in m.provider.read(m.row["kind"], m.row["collection_id"])}
                unseen = moved - listed
            except (AuthError, ApiError):
                unseen = set(moved)
        with self.db.tx() as c:
            c.executemany("UPDATE member_items SET confirmed=0, misses=0, missing_since=NULL "
                          "WHERE member_id=? AND song_id=?", [(m.id, sid) for sid in unseen])
            c.executemany("UPDATE member_items SET confirmed=0, misses=?, missing_since=NULL "
                          "WHERE member_id=? AND song_id=?",
                          [(PENDING_RETRY_AFTER - 1, m.id, sid) for sid in lost])
