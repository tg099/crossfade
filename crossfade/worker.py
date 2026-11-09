"""Background execution of line runs: scheduler, per-user serialization, live progress events."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any

from . import backup, health, history, notify, updates
from .config import config
from .db import Database, get_db
from .engine import LineSync, Stopped
from .models import ApiError, AuthError
from .providers import build_provider

log = logging.getLogger("crossfade.worker")
action_context: ContextVar[str | None] = ContextVar("mcp_action", default=None)


def _finish_action(db: Database, action_id: str | None, status: str, result: dict, message: str | None) -> None:
    if action_id:
        db.run("UPDATE mcp_actions SET state=?,result=?,error=? WHERE id=? AND state IN ('executing','queued')",
               (status, json.dumps(result), message, action_id))


class EventBus:
    """Fan-out of events to the SSE connections of one user. Connections wait on asyncio queues in
    the server's event loop (no thread per open app); publishing works from any thread."""

    def __init__(self):
        self._subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: int) -> asyncio.Queue:
        """Call from inside the event loop."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.setdefault(user_id, []).append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(user_id, [])
            subs[:] = [s for s in subs if s[1] is not q]
            if not subs:
                self._subs.pop(user_id, None)

    def publish(self, user_id: int, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(user_id, []))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, event)
            except RuntimeError:      # the loop is closed (server shutting down)
                pass


def _offer(q: asyncio.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:         # a stalled connection misses events instead of piling them up
        pass


class Worker:
    def __init__(self, db: Database | None = None):
        self.db = db or get_db()
        self.bus = EventBus()
        self.pool = ThreadPoolExecutor(max_workers=config.worker_threads, thread_name_prefix="sync")
        self.jobs: dict[int, dict[str, Any]] = {}          # line_id -> live state
        self.live: dict[int, list[dict]] = {}              # line_id -> results of the running job so far
        self.catalog_jobs: dict[int, dict[str, Any]] = {}  # user_id -> live state of a catalog job
        self._catalog_stop: dict[int, threading.Event] = {}
        self._stop_flags: dict[int, threading.Event] = {}
        self._user_locks: dict[int, threading.Lock] = {}
        self._lock = threading.Lock()
        self._scheduler: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._checking: set[int] = set()                   # accounts whose login is being tried
        self._house = None                                 # the running daily backup / update check
        self.restoring = False                             # an admin restores a backup: start nothing

    def busy(self) -> bool:
        """Anything that writes to the database is running (a restore waits for a quiet moment)."""
        return bool(self.jobs or self.catalog_jobs or self._checking or (self._house and not self._house.done()))

    def begin_restore(self) -> bool:
        with self._lock:
            if self.restoring or self.busy():
                return False
            self.restoring = True
            return True

    def end_restore(self) -> None:
        self.restoring = False

    # ------------------------------------------------------------------ public
    def start_scheduler(self) -> None:
        if self._scheduler is None:
            self._scheduler = threading.Thread(target=self._schedule_loop, name="scheduler", daemon=True)
            self._scheduler.start()

    def shutdown(self) -> None:
        self._shutdown.set()
        for ev in [*self._stop_flags.values(), *self._catalog_stop.values()]:
            ev.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def job(self, line_id: int) -> dict | None:
        return self.jobs.get(line_id)

    def live_results(self, line_id: int, since: int = 0) -> dict:
        """Results of the running job of a line, from entry `since` on (the job's `live_n` says how many exist)."""
        job = self.jobs.get(line_id)
        items = self.live.get(line_id) or []
        return {"running": job is not None, "started_at": job["started_at"] if job else None,
                "n": len(items), "items": items[max(since, 0):]}

    def start(self, line_id: int, trigger: str = "manual", apply: bool = True,
              allow_deletions: bool = False) -> bool:
        line = self.db.one("SELECT id, user_id FROM lines WHERE id=?", (line_id,))
        if not line:
            return False
        with self._lock:
            if line_id in self.jobs or self.restoring:
                return False
            self._stop_flags[line_id] = threading.Event()
            self.jobs[line_id] = {"line_id": line_id, "state": "queued", "mode": "apply" if apply else "preview",
                                  "trigger": trigger, "phase": "Waiting …", "done": 0, "total": 0,
                                  "started_at": time.time(), "live_n": 0, "mcp_action_id": action_context.get()}
            self.live[line_id] = []
        self._emit(line["user_id"], line_id)
        self.pool.submit(self._run, line_id, line["user_id"], trigger, apply, allow_deletions)
        return True

    def stop(self, line_id: int) -> None:
        ev = self._stop_flags.get(line_id)
        if ev:
            ev.set()

    # ------------------------------------------------------------------ catalog
    def catalog_job(self, user_id: int) -> dict | None:
        return self.catalog_jobs.get(user_id)

    def start_catalog(self, user_id: int, action: str, params: dict | None = None) -> bool:
        """Catalog jobs (scan / bulk remove / bulk add) run one at a time per user, after any line run."""
        with self._lock:
            if user_id in self.catalog_jobs or self.restoring:
                return False
            self._catalog_stop[user_id] = threading.Event()
            self.catalog_jobs[user_id] = {"action": action, "state": "queued", "phase": "Waiting …", "done": 0,
                                          "total": 0, "started_at": time.time(), "mcp_action_id": action_context.get()}
        self._emit_catalog(user_id)
        self.pool.submit(self._run_catalog, user_id, action, params or {})
        return True

    def stop_catalog(self, user_id: int) -> None:
        ev = self._catalog_stop.get(user_id)
        if ev:
            ev.set()

    def _emit_catalog(self, user_id: int, finished: dict | None = None) -> None:
        ev: dict[str, Any] = {"type": "catalog", "job": self.catalog_jobs.get(user_id)}
        if finished:
            ev["finished"] = finished
        self.bus.publish(user_id, ev)

    def _run_catalog(self, user_id: int, action: str, params: dict) -> None:
        from .catalog import Stopped as CatalogStopped
        from .cleanup import Cleanup
        db = self.db
        stop = self._catalog_stop[user_id]
        last_emit = [0.0]

        def progress(phase: str, done: int, total: int) -> None:
            job = self.catalog_jobs.get(user_id)
            if job:
                job.update(state="running", phase=phase, done=done, total=total)
                if time.time() - last_emit[0] > 0.25 or done == total:
                    last_emit[0] = time.time()
                    self._emit_catalog(user_id)

        status, message, result = "ok", None, {}
        with self._user_lock(user_id):
            try:
                progress("Connecting to the services …", 0, 0)
                providers, auth_errors = {}, {}
                for acc in db.all("SELECT * FROM accounts WHERE user_id=? AND credentials IS NOT NULL", (user_id,)):
                    try:
                        providers[acc["id"]] = build_provider(db, acc)
                    except AuthError as e:
                        auth_errors[acc["id"]] = str(e)
                cat = Cleanup(db, user_id, providers, progress, stop.is_set)
                cat.auth_errors.update(auth_errors)
                if action == "scan":
                    result = cat.scan()
                elif action == "remove":
                    result = cat.remove(set(params["song_ids"]), params["source_ids"])
                elif action == "add":
                    result = cat.add(params["song_ids"], params.get("source_id"), params.get("account_id"),
                                     params.get("new_playlist"))
                elif action == "dedupe":
                    result = cat.dedupe(params.get("groups") or [], params.get("copies") or [])
                elif action == "swap":
                    result = cat.swap(params["items"])
                elif action == "playlists":
                    result = cat.playlists(params.get("delete") or [], params.get("merge") or [])
                elif action == "undo":
                    result = cat.undo()
                elif action == "restore":
                    try:
                        result = history.restore(db, user_id, providers, params["snapshot_id"], params["mode"],
                                                 params.get("only"), progress, stop.is_set)
                    except history.Stopped:
                        raise CatalogStopped() from None
                else:
                    raise ValueError(action)
                for acc_id, msg in cat.auth_errors.items():
                    self._mark_account(acc_id, "error", msg)
                if cat.auth_errors or result.get("errors") or result.get("failed") or result.get("missing"):
                    status = "partial"
            except CatalogStopped:
                status, message = "stopped", "Stopped"
            except AuthError as e:
                status, message = "auth_error", str(e)
            except ApiError as e:
                status, message = "error", str(e)
            except Exception as e:  # noqa: BLE001
                log.error("catalog %s failed: %s", action, traceback.format_exc())
                status, message = "error", f"{type(e).__name__}: {e}"
            finally:
                if action == "scan" and status in ("ok", "partial"):
                    db.set_setting(f"catalog_scanned_{user_id}", time.time())
                _finish_action(db, (self.catalog_jobs.get(user_id) or {}).get("mcp_action_id"), status, result, message)
                with self._lock:
                    self.catalog_jobs.pop(user_id, None)
                    self._catalog_stop.pop(user_id, None)
                self._emit_catalog(user_id, {"action": action, "status": status, "message": message,
                                             "result": result})

    # ------------------------------------------------------------------ internals
    def _emit(self, user_id: int, line_id: int, extra: dict | None = None) -> None:
        """Progress of a run goes to everyone in the fade (the owner and friends who joined)."""
        ev = {"type": "job", "line_id": line_id, "job": self.jobs.get(line_id)}
        if extra:
            ev.update(extra)
        for uid in self._audience(line_id, user_id):
            self.bus.publish(uid, ev)

    def _audience(self, line_id: int, owner: int) -> set[int]:
        from .sharing import participants
        try:
            return participants(self.db, line_id) | {owner}
        except Exception:  # noqa: BLE001 – the line may just have been deleted
            return {owner}

    def _user_lock(self, user_id: int) -> threading.Lock:
        with self._lock:
            return self._user_locks.setdefault(user_id, threading.Lock())

    def _run(self, line_id: int, user_id: int, trigger: str, apply: bool, allow_deletions: bool) -> None:
        stop = self._stop_flags[line_id]
        last_emit = [0.0]

        def progress(phase: str, done: int, total: int) -> None:
            job = self.jobs.get(line_id)
            if job:
                job.update(state="running", phase=phase, done=done, total=total)
                if time.time() - last_emit[0] > 0.25 or done == total:
                    last_emit[0] = time.time()
                    self._emit(user_id, line_id)

        def live(kind: str, data: dict) -> None:
            items, job = self.live.get(line_id), self.jobs.get(line_id)
            if items is None or job is None:
                return
            items.append({"kind": kind, **data})
            job["live_n"] = len(items)
            if kind == "start" or time.time() - last_emit[0] > 0.25:
                last_emit[0] = time.time()
                self._emit(user_id, line_id)

        # one run per account holder at a time (rate limits, consistency); a shared fade waits for
        # everyone whose services it uses, always locked in the same order
        audience = sorted(self._audience(line_id, user_id))
        locks = [self._user_lock(uid) for uid in audience]
        for lock in locks:
            lock.acquire()
        try:
            self._run_locked(line_id, user_id, trigger, apply, allow_deletions, stop, progress, live)
        finally:
            for lock in reversed(locks):
                lock.release()
        self._cleanup_runs(line_id)

    def _run_locked(self, line_id: int, user_id: int, trigger: str, apply: bool, allow_deletions: bool,
                    stop: threading.Event, progress, live) -> None:
        db = self.db
        run_id = None
        status, message, summary, plan = "error", None, {}, {}
        try:
            self.jobs[line_id].update(state="running", phase="Connecting to the services …")
            self._emit(user_id, line_id)
            run_id = db.run("INSERT INTO runs(line_id,trigger,started_at,status) VALUES(?,?,?, 'running')",
                            (line_id, "preview" if not apply else trigger, time.time()))
            providers, failed_account = {}, None
            for acc in db.all("""SELECT DISTINCT a.* FROM accounts a JOIN line_members m ON m.account_id=a.id
                                 WHERE m.line_id=?""", (line_id,)):
                try:
                    providers[acc["id"]] = build_provider(db, acc)
                except AuthError as e:
                    failed_account = (acc, str(e))
                    break
            if failed_account:
                acc, msg = failed_account
                self._mark_account(acc["id"], "error", msg)
                status, message = "auth_error", msg
            else:
                sync = LineSync(db, line_id, providers, progress, stop.is_set, run_id, live)
                res = sync.run(apply=apply, allow_deletions=allow_deletions)
                status, message, summary, plan = res.status, res.message, res.summary, res.plan
                if status == "auth_error":
                    if sync.active_account:
                        self._mark_account(sync.active_account, "error", message)
                    else:
                        self._flag_auth(line_id, message or "")
                elif status not in ("error",):
                    for acc_id in providers:
                        self._mark_account(acc_id, "ok", None)
        except Stopped:
            status, message = "stopped", "Stopped"
        except Exception as e:  # noqa: BLE001
            log.error("run failed: %s", traceback.format_exc())
            status, message = "error", f"{type(e).__name__}: {e}"
        finally:
            summary = dict(summary or {})
            if message:
                summary["message"] = message
            if run_id:
                db.run("UPDATE runs SET finished_at=?, status=?, summary=?, plan=? WHERE id=?",
                       (time.time(), status, json.dumps(summary), json.dumps(plan) if plan else None, run_id))
            if apply or status in ("auth_error", "blocked", "error"):
                db.run("UPDATE lines SET last_run_at=?, last_status=?, last_summary=? WHERE id=?",
                       (time.time(), status, json.dumps(summary), line_id))
            _finish_action(db, (self.jobs.get(line_id) or {}).get("mcp_action_id"), status,
                           {"run_id": run_id, "summary": summary, "plan": plan}, message)
            with self._lock:
                self.jobs.pop(line_id, None)
                self.live.pop(line_id, None)
                self._stop_flags.pop(line_id, None)
            if apply:
                notify.safely(notify.run_finished, db, line_id, status, message, trigger)
            finished = {"type": "job", "line_id": line_id, "job": None,
                        "finished": {"status": status, "message": message, "summary": summary,
                                     "preview": not apply}}
            for uid in self._audience(line_id, user_id):
                self.bus.publish(uid, finished)

    def _mark_account(self, account_id: int, status: str, message: str | None) -> None:
        mark_account(self.db, account_id, status, message)

    def _flag_auth(self, line_id: int, message: str) -> None:
        """Mark the account whose service is named in the auth error message."""
        names = {"youtube": "ytm", "apple": "apple", "spotify": "spotify", "google": "ytm", "tidal": "tidal",
                 "deezer": "deezer"}
        low = message.lower()
        services = {svc for key, svc in names.items() if key in low}
        for acc in self.db.all("""SELECT DISTINCT a.id, a.service FROM accounts a
                                  JOIN line_members m ON m.account_id=a.id WHERE m.line_id=?""", (line_id,)):
            if acc["service"] in services:
                self._mark_account(acc["id"], "error", message)

    def _cleanup_runs(self, line_id: int, keep: int = 100) -> None:
        self.db.run("""DELETE FROM runs WHERE line_id=? AND id NOT IN
                       (SELECT id FROM runs WHERE line_id=? ORDER BY id DESC LIMIT ?)""", (line_id, line_id, keep))
        self.db.run("""DELETE FROM changes WHERE line_id=? AND id NOT IN
                       (SELECT id FROM changes WHERE line_id=? ORDER BY id DESC LIMIT 5000)""", (line_id, line_id))

    def _schedule_loop(self) -> None:
        while not self._shutdown.wait(20):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.error("scheduler: %s", traceback.format_exc())

    def _tick(self) -> None:
        if self.restoring:
            return
        health.flush(self.db)
        self._check_accounts()
        self._housekeeping()
        now = time.time()
        due = self.db.all("""
            SELECT l.id FROM lines l
            WHERE l.paused=0 AND l.interval_minutes>0
              AND (l.last_run_at IS NULL OR l.last_run_at + l.interval_minutes*60 <= ?)
              AND (SELECT COUNT(*) FROM line_members m WHERE m.line_id=l.id) >= 2
              AND NOT EXISTS (SELECT 1 FROM line_members m JOIN accounts a ON a.id=m.account_id
                              WHERE m.line_id=l.id AND (a.status='error' OR a.credentials IS NULL))
            ORDER BY l.last_run_at IS NOT NULL, l.last_run_at""", (now,))
        for row in due:
            self.start(row["id"], trigger="schedule")


    def _housekeeping(self) -> None:
        """A daily backup and the update check, in the background so syncs aren't held up."""
        if self._house and not self._house.done():
            return
        need_backup = config.backup_daily and backup.daily_due(self.db)
        need_update = updates.due(self.db)
        if need_backup or need_update:
            self._house = self.pool.submit(self._house_run, need_backup, need_update)

    def _house_run(self, need_backup: bool, need_update: bool) -> None:
        try:
            if need_backup and not self.restoring:
                self.db.backup("daily")
            if need_update:
                updates.check(self.db)
        except Exception:  # noqa: BLE001
            log.error("housekeeping: %s", traceback.format_exc())

    # ------------------------------------------------------------------ login checks
    def _check_accounts(self) -> None:
        """Try every working login about twice a day, also those no fade uses: an expired login is
        noticed (and reported) before a sync runs into it. One account per tick, never one that a
        sync of its owner is using right now."""
        due = self.db.one("""SELECT id, user_id FROM accounts WHERE credentials IS NOT NULL AND status='ok'
                              AND (checked_at IS NULL OR checked_at < ?) ORDER BY checked_at IS NOT NULL, checked_at
                              LIMIT 1""", (time.time() - CHECK_EVERY,))
        with self._lock:
            if not due or due["id"] in self._checking or self.restoring:
                return
            self._checking.add(due["id"])
        self.pool.submit(self._check_one, due["id"], due["user_id"])

    def _check_one(self, account_id: int, user_id: int) -> None:
        try:
            with self._user_lock(user_id):
                check_account(self.db, account_id)
        except Exception:  # noqa: BLE001
            log.error("login check: %s", traceback.format_exc())
        finally:
            self._checking.discard(account_id)


CHECK_EVERY = 12 * 3600
EXPIRY_WARNING = 7 * 86400


def mark_account(db: Database, account_id: int, status: str, message: str | None) -> None:
    """Record whether a login works; tell its owner once when it stops working."""
    was = db.val("SELECT status FROM accounts WHERE id=?", (account_id,))
    db.run("UPDATE accounts SET status=?, status_message=? WHERE id=?", (status, message, account_id))
    if status == "error" and was != "error":
        notify.safely(notify.account_failed, db, account_id, message)
    elif status == "ok":
        notify.safely(notify.account_ok, db, account_id)


def check_account(db: Database, account_id: int) -> str:
    """Sign in once (whoami) and read when the login ends. Returns ok | error | unreachable."""
    acc = db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not acc or not acc["credentials"]:
        return "error"
    result = "ok"
    try:
        prov = build_provider(db, acc)
        prov.whoami()
        expires = prov.login_expires_at()
        db.run("UPDATE accounts SET checked_at=?, expires_at=? WHERE id=?", (time.time(), expires, account_id))
        mark_account(db, account_id, "ok", None)
        if expires and expires - time.time() < EXPIRY_WARNING:
            notify.safely(notify.account_expiring, db, account_id, expires)
    except AuthError as e:
        db.run("UPDATE accounts SET checked_at=? WHERE id=?", (time.time(), account_id))
        mark_account(db, account_id, "error", str(e))
        result = "error"
    except Exception as e:  # noqa: BLE001 – the service is down or slow: try again next time
        log.info("login check of account %s: %s", account_id, e)
        db.run("UPDATE accounts SET checked_at=? WHERE id=?", (time.time() - CHECK_EVERY + 3600, account_id))
        result = "unreachable"
    return result


_worker: Worker | None = None


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker
